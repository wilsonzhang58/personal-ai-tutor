import json,re,math,hashlib
from uuid import uuid4
from pydantic import BaseModel
from sqlalchemy import select
from . import models as m
from .llm import client,configured,model_name
from .knowledge import snapshot
from .mastery import settle
from .locks import task_lock

class PracticeOutput(BaseModel):
    question:str
    answer:str
    hint:str
    rubric:str
    difficulty:int
    numeric_answer:float | None
    verification_expression:str | None

class GradeOutput(BaseModel):
    correct:bool | None
    reliable:bool
    basic_error:bool
    rubric_covered:bool
    feedback:str
    difficulty_supported: bool = True

def public(db,generation,cache_hit=False):
    item=db.get(m.Exercise,generation.exercise_id)
    return {'id':item.id,'question':item.question,'source':'model_generated_reference','difficulty':generation.metrics.get('difficulty',0),'assisted':generation.metrics.get('assisted',False),'graded':generation.metrics.get('graded',False),'usage':{'input_tokens':0,'output_tokens':0} if cache_hit else generation.metrics.get('usage',{}),'cache_hit':cache_hit,'model_calls':0 if cache_hit else 1}

def generate(db,course_id,concept_id,operation_id,new_question=False):
    with task_lock('practice:'+course_id+':'+concept_id):
        db.rollback();db.expire_all()
        old=db.get(m.PracticeGeneration,operation_id)
        if old:return public(db,old,True)
        for g in db.scalars(select(m.PracticeGeneration).order_by(m.PracticeGeneration.operation_id)).all():
            if g.metrics.get('course_id')==course_id and g.metrics.get('concept_id')==concept_id and not g.metrics.get('graded') and not g.metrics.get('skipped'):
                if not new_question:return public(db,g,True)
                g.metrics={**g.metrics,'skipped':True}
        if not configured():raise ValueError('生成练习需要真实 API 配置')
        link=db.get(m.CourseConcept,(course_id,concept_id))
        if not link:raise ValueError('知识点不属于本课程')
        source=db.get(m.SourceChunk,link.source_chunk_id) if link.source_chunk_id else None
        level=snapshot(db,concept_id)['mastery_level']
        response=client().responses.parse(model=model_name(),instructions='中文生成一道可独立作答的练习、提示、参考解、评分准则。难度对应指定等级：0识别，1基础计算，2方法运用，3应用，4迁移综合，5更深入迁移。difficulty 是0–5，不是外部校准分数。等级3必须应用题，等级4至少迁移综合题。优先可核验数值题，若只需一个数字答案，填写 numeric_answer 及仅用数字和四则运算的 verification_expression，用于独立算术核验，否则两者 null。数学用$和$$。不能省略参考推导。',input=json.dumps({'concept':db.get(m.Concept,concept_id).name,'level':level,'source':source.text[:1800] if source else ''},ensure_ascii=False),text_format=PracticeOutput,max_output_tokens=1800)
        output=response.output_parsed
        if output is None:raise ValueError('练习生成未完成')
        item=m.Exercise(id=str(uuid4()),concept_id=concept_id,question=output.question,answer=output.answer,hint=output.hint);db.add(item);db.flush()
        metrics={'source':'model_generated_reference','course_id':course_id,'concept_id':concept_id,'model':model_name(),'difficulty':max(0,min(5,output.difficulty)),'rubric':output.rubric,'numeric_answer':output.numeric_answer,'verification_expression':output.verification_expression,'assisted':False,'graded':False,'usage':response.usage.model_dump() if response.usage else {}}
        generation=m.PracticeGeneration(operation_id=operation_id,exercise_id=item.id,metrics=metrics);db.add(generation);db.commit()
        return public(db,generation)

def generation_for(db,exercise_id):
    return db.scalar(select(m.PracticeGeneration).where(m.PracticeGeneration.exercise_id==exercise_id))

def reveal(db,exercise_id,field):
    with task_lock('attempt:'+exercise_id):
        db.rollback();g=generation_for(db,exercise_id);item=db.get(m.Exercise,exercise_id)
        if not item:raise ValueError('题目不存在')
        if g and not g.metrics.get('graded'):g.metrics={**g.metrics,'assisted':True}
        db.commit();return {'text':item.hint if field=='hint' else item.answer}

def grade(db,exercise_id,operation_id,response,hint_used=False):
    with task_lock('attempt:'+exercise_id):
        db.rollback();db.expire_all()
        prior=db.scalar(select(m.Attempt).where(m.Attempt.exercise_id==exercise_id))
        if prior:
            if 'mastery' not in prior.data:
                data=prior.data;item=db.get(m.Exercise,exercise_id)
                settled=settle(db,'practice:'+exercise_id,item.concept_id,'practice',{'identity':data.get('evidence_identity',exercise_id),'state_version_used':data.get('state_version_used'),'correct':data.get('correct'),'reliable':data.get('reliable') and data.get('rubric_covered'),'assisted':data.get('assisted'),'difficulty':data.get('difficulty',0) if data.get('difficulty_supported',True) else 0,'basic_error':data.get('basic_error')})
                prior=db.get(m.Attempt,prior.id);prior.data={**data,'mastery':settled};db.commit()
            return {'id':prior.id,'feedback':prior.feedback,**prior.data,'duplicate':True,'model_calls':0,'original_usage':prior.data.get('usage',{}),'usage':{'input_tokens':0,'output_tokens':0}}
        item=db.get(m.Exercise,exercise_id);g=generation_for(db,exercise_id)
        if not item:raise ValueError('题目不存在')
        version_used=snapshot(db,item.concept_id)['state_version']
        meta=g.metrics if g else {};numeric=meta.get('numeric_answer');usage={}
        # Numeric checker does not trust arbitrary expressions or execute student input.
        if numeric is not None and snapshot(db,item.concept_id)['mastery_level']<3 and verified_numeric(meta) and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',response.strip()):
            correct=math.isclose(float(response.strip()),numeric,rel_tol=1e-6,abs_tol=1e-6)
            assessed=GradeOutput(correct=correct,reliable=True,basic_error=False,rubric_covered=True,feedback='数值答案与参考值一致。' if correct else '数值未匹配，请检查计算步骤。')
            grade_source='numeric_against_generated_reference';grade_calls=0
        else:
            r=client().responses.parse(model=model_name(),instructions='按题目和评分准则给出审慎反馈。核查参考解是否合理；答案/题意不可靠时 correct=null, reliable=false。只有明确基础错误才 basic_error=true。当前等级3及以上需额外核查题目是否确实考查应用（3→4）或迁移综合（4→5），普通定义/简单计算不足则 difficulty_supported=false，不可因难度标签高就通过。说明 rubric_covered；不确定时不改等级，不使用概率置信。',input=json.dumps({'question':item.question,'reference':item.answer,'rubric':meta.get('rubric',''),'answer':response,'current_level':snapshot(db,item.concept_id)['mastery_level'],'declared_difficulty':meta.get('difficulty',0)},ensure_ascii=False),text_format=GradeOutput,max_output_tokens=1100)
            assessed=r.output_parsed
            if assessed is None:raise ValueError('评分未完成')
            usage=r.usage.model_dump() if r.usage else {};grade_source='model_rubric';grade_calls=1
        evidence_identity='question:'+hashlib.sha256(re.sub(r'\s+','',item.question).encode()).hexdigest()
        data={**assessed.model_dump(),'evidence_identity':evidence_identity,'state_version_used':version_used,'usage':usage,'source':grade_source,'model_calls':grade_calls,'assisted':bool(hint_used or meta.get('assisted') or not meta.get('rubric')),'difficulty':meta.get('difficulty',0)}
        row=m.Attempt(id=str(uuid4()),operation_id=operation_id,exercise_id=exercise_id,response=response,feedback=assessed.feedback,hint_used=data['assisted'],source=grade_source,data=data)
        db.add(row)
        if g:g.metrics={**meta,'graded':True}
        db.commit()
        result=settle(db,'practice:'+exercise_id,item.concept_id,'practice',{'identity':evidence_identity,'state_version_used':version_used,'correct':assessed.correct,'reliable':assessed.reliable and assessed.rubric_covered,'assisted':data['assisted'],'difficulty':data['difficulty'] if assessed.difficulty_supported else 0,'basic_error':assessed.basic_error})
        row=db.get(m.Attempt,row.id);row.data={**data,'mastery':result};db.commit()
        return {'id':row.id,'feedback':row.feedback,**row.data}

def verified_numeric(meta):
    import ast,operator
    operations={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}
    def calculate(node):
        if isinstance(node,ast.Constant) and isinstance(node.value,(int,float)):return node.value
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):return -calculate(node.operand)
        if isinstance(node,ast.BinOp) and type(node.op) in operations:return operations[type(node.op)](calculate(node.left),calculate(node.right))
        raise ValueError('Unsafe arithmetic')
    try:
        expression=meta.get('verification_expression') or ''
        if len(expression)>300:return False
        result=calculate(ast.parse(expression,mode='eval').body)
        return math.isfinite(result) and math.isclose(result,meta['numeric_answer'],rel_tol=1e-7,abs_tol=1e-7)
    except (ValueError,SyntaxError,ZeroDivisionError,TypeError):return False
