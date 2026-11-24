import json
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import time
import platform
import hashlib
import math
from fastapi import FastAPI, Depends, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from .db import Base, engine, get_db, SessionLocal
from . import models as m
from .knowledge import apply_event, snapshot
from .teaching import make_plan
from .ingestion import preview, confirm
from .graph import run_graph
from .llm import configured, model_name, key_status, demo_requested

INSTANCE_ID = str(uuid4())
app = FastAPI(title='Personal Tutor Agent')
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'], allow_methods=['*'], allow_headers=['*'])

class KnowledgeInput(BaseModel):
    client_operation_id: str
    concept_id: str
    familiarity: str | None = None
    support_override: str | None = None
    expected_version: int | None = None
    reason: str = 'self_report'
    mastery_level: int | None = None

class TurnInput(BaseModel):
    client_operation_id: str
    action: str
    course_id: str
    concept_id: str
    lesson_id: str | None = None
    thread_id: str | None = None
    selection: dict | None = None
    message: str = ''
    explicit_support: str | None = None

class NoteInput(BaseModel):
    client_operation_id: str
    lesson_id: str
    block_id: str
    anchor: dict
    text: str

class ProgressInput(BaseModel):
    client_operation_id: str
    concept_id: str
    lesson_id: str | None = None
    block_id: str | None = None
    offset: int = 0
    completed: bool = False

    @field_validator('offset', mode='before')
    @classmethod
    def rounded_offset(cls, value):
        number=float(value)
        if not math.isfinite(number):raise ValueError('Invalid offset')
        return max(0,round(number))

class MaterialInput(BaseModel):
    pages: list[str]
    expected_version: int | None = None

class CourseInput(BaseModel):
    title: str
    description: str = ''

class PlanInput(BaseModel):
    concepts: list[dict]
    prerequisites: list[dict] = Field(default_factory=list)

class OperationInput(BaseModel):
    client_operation_id: str
    new_question: bool = False

class AttemptInput(BaseModel):
    client_operation_id: str
    response: str
    hint_used: bool = False

@app.on_event('startup')
def startup():
    from .migration import migrate
    migrate()
    with SessionLocal() as db:
        if db.get(m.Profile, 'local_user') is None:
            db.add(m.Profile(id='local_user'))
            db.commit()
        if db.get(m.Course, 'linear_algebra_demo') is None:
            seed_demo(db)
        for pending in db.scalars(select(m.Turn).where(m.Turn.status == 'pending')).all():
            pending.status = 'interrupted'
            pending.error = 'Generation was interrupted; retry the same operation ID.'
        db.commit()
        from .incremental import bootstrap
        bootstrap(db)
        from .resolver import seed_aliases
        seed_aliases(db)
        from .classification import ensure
        ensure(db)
        for job in db.scalars(select(m.LessonGeneration).where(m.LessonGeneration.status.in_(['queued','running']))).all():
            job.status='interrupted';job.error='服务已重启，点击开始学习可重试。'
        db.commit()

def seed_demo(db):
    root = Path(__file__).resolve().parents[2] / 'data' / 'demo'
    spec = json.loads((root / 'concepts.json').read_text())
    for course in spec['courses']:
        db.add(m.Course(id=course['id'], title=course['title'], description='自编 Demo 课程，可替换为自己的课件'))
        db.flush()
        for index, (concept_id, name) in enumerate(course['concepts']):
            if not db.get(m.Concept, concept_id):
                db.add(m.Concept(id=concept_id, name=name))
                db.flush()
            db.add(m.CourseConcept(course_id=course['id'], concept_id=concept_id, position=index))
        body = (root / course['file']).read_bytes()
        material = preview(db, course['id'], course['file'], body)
        confirm(db, material, material.pages)
    for course_id, concept_id, prerequisite_id in spec['prerequisites']:
        db.add(m.Prerequisite(course_id=course_id, concept_id=concept_id, prerequisite_id=prerequisite_id))
    for concept_id, question, answer, hint in [
        ('linear_algebra.elementary_row_operations', '对 [[1,1,3],[2,3,8]] 做 R2←R2−2R1，第二行是什么？', '[0,1,2]', '对第二行三个数字都相减。'),
        ('optimization.simplex_tableau_pivot', '主元行 [2,1,4] 除以主元 2 后是什么？', '[1,0.5,2]', '整行都除以 2。'),
        ('optimization.slack_variable', 'x+y≤4 引入松弛变量后等式是什么？', 'x+y+s=4', '新增非负变量。')]:
        db.add(m.Exercise(id=str(uuid4()), concept_id=concept_id, question=question, answer=answer, hint=hint))
    db.commit()

@app.get('/health')
def health():
    return {'status': 'ok', 'demo_mode': demo_requested(), 'model': model_name(), 'api_key_configured': key_status()['present'] and not key_status()['placeholder'], 'key_status': key_status(), 'instance_id': INSTANCE_ID, 'python': platform.python_version()}

@app.get('/v1/courses')
def courses(db: Session = Depends(get_db)):
    result = []
    for course in db.scalars(select(m.Course)).all():
        links = db.scalars(select(m.CourseConcept).where(m.CourseConcept.course_id == course.id).order_by(m.CourseConcept.position)).all()
        result.append({'id': course.id, 'title': course.title, 'folder':db.scalar(select(m.CourseFolder.folder).where(m.CourseFolder.course_id==course.id)), 'description': course.description, 'concepts': [{'id': link.concept_id, 'name': db.get(m.Concept, link.concept_id).name, 'topic_id': link.topic_id or (course.id+':'+link.concept_id), 'completed': bool(db.scalar(select(m.StudyRun).where(m.StudyRun.course_id==course.id,m.StudyRun.concept_id==link.concept_id,m.StudyRun.completed==True))) or any(e.after.get('course_id')==course.id for e in db.scalars(select(m.LearnerEvent).where(m.LearnerEvent.concept_id==link.concept_id,m.LearnerEvent.kind=='concept_completed')).all())} for link in links]})
    return result

@app.post('/v1/courses')
def create_course(body: CourseInput, db: Session = Depends(get_db)):
    course = m.Course(id=str(uuid4()), title=body.title, description=body.description)
    db.add(course); db.commit()
    return {'id': course.id, 'title': course.title}

class FolderInput(BaseModel):
    name: str
    course_id: str | None = None

@app.get('/v1/folders')
def folders(db: Session = Depends(get_db)):
    from .storage import discover
    return discover(db)

@app.post('/v1/folders')
def prepare_folder(body: FolderInput, db: Session = Depends(get_db)):
    from .storage import bind_folder
    mapping=bind_folder(db,body.name,body.course_id)
    return {'folder':mapping.folder,'course_id':mapping.course_id}

@app.post('/v1/folders/{folder}/materials')
def batch_upload(folder: str, files: list[UploadFile], db: Session = Depends(get_db)):
    from .storage import upload_one, ROOT
    mapping=db.get(m.CourseFolder,folder)
    if not mapping: raise HTTPException(404,'先选择或创建课程目录')
    results=[]
    for file in files:
        try:
            content=file.file.read(10*1024*1024+1)
            material,status,path=upload_one(db,mapping,file.filename or '',content)
            confirm(db,material,material.pages)
            results.append({'filename':file.filename,'status':status,'id':material.id,'pages':material.pages,'confirmed':material.confirmed,'saved_path':str(path.relative_to(ROOT))})
        except Exception as exc:
            db.rollback()
            results.append({'filename':file.filename,'status':'failed','error':exc.detail if isinstance(exc,HTTPException) else type(exc).__name__})
    return {'course_id':mapping.course_id,'folder':folder,'files':results}

@app.post('/v1/courses/{course_id}/materials')
async def upload(course_id: str, file: UploadFile, db: Session = Depends(get_db)):
    if not db.get(m.Course, course_id): raise HTTPException(404, 'Course not found')
    from .storage import upload_one
    mapping=db.scalar(select(m.CourseFolder).where(m.CourseFolder.course_id==course_id))
    if mapping:
        material,status,path=upload_one(db,mapping,file.filename or '',await file.read(10*1024*1024+1))
    else:
        material=preview(db,course_id,file.filename or '',await file.read(10*1024*1024+1))
    return {'id':material.id,'pages':material.pages,'confirmed':material.confirmed}

@app.get('/v1/courses/{course_id}/materials')
def list_materials(course_id: str, db: Session = Depends(get_db)):
    from .storage import original_path
    return [{'id': r.id, 'title': r.title, 'version': r.version, 'confirmed': r.confirmed, 'pages': r.pages, 'saved_path': str(original_path(db,r))} for r in db.scalars(select(m.Material).where(m.Material.course_id == course_id)).all()]

@app.get('/v1/materials/{material_id}/pages/{page}')
def material_page(material_id: str, page: int, db: Session = Depends(get_db)):
    material=db.get(m.Material,material_id)
    if not material or page<1 or page>len(material.pages): raise HTTPException(404,'Page not found')
    from .storage import original_path
    path=original_path(db,material)
    if not path.exists() or path.suffix.lower()!='.pdf': raise HTTPException(404,'No PDF original for this material')
    import pymupdf
    with pymupdf.open(path) as document:
        png=document[page-1].get_pixmap(matrix=pymupdf.Matrix(1.6,1.6)).tobytes('png')
    return Response(png,media_type='image/png')

@app.patch('/v1/materials/{material_id}')
def confirm_material(material_id: str, body: MaterialInput, db: Session = Depends(get_db)):
    material = db.get(m.Material, material_id)
    if not material: raise HTTPException(404, 'Material not found')
    if body.expected_version is not None and body.expected_version!=material.version:raise HTTPException(409,'课件已被修改，请重新打开后再保存')
    if material.confirmed and len(body.pages)!=len(material.pages):raise HTTPException(422,'修正原文不能更改页数')
    confirm(db, material, body.pages)
    return {'id': material.id, 'version': material.version, 'confirmed': True}

@app.post('/v1/courses/{course_id}/sync')
def sync_materials(course_id: str, reorder: bool=False, db: Session = Depends(get_db)):
    if not db.get(m.Course,course_id):raise HTTPException(404,'Course not found')
    from .incremental import sync_course
    try:return sync_course(db,course_id,reorder)
    except Exception as exc:
        db.rollback()
        detail=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        raise HTTPException(502,'资料已保存，学习路线更新失败，可重试：'+detail) from exc

@app.post('/v1/courses/{course_id}/plan')
def suggest_plan(course_id: str, db: Session = Depends(get_db)):
    from .planning import extract_plan
    return extract_plan(db, course_id)

@app.put('/v1/courses/{course_id}/plan')
def save_plan(course_id: str, body: PlanInput, db: Session = Depends(get_db)):
    if not db.get(m.Course, course_id): raise HTTPException(404, 'Course not found')
    ids=[item.get('id') for item in body.concepts]
    if not ids or len(ids)!=len(set(ids)) or any(not isinstance(cid,str) or not cid for cid in ids): raise HTTPException(422,'计划需要非空且不重复的概念 ID')
    for item in body.concepts:
        chunk=db.get(m.SourceChunk,item['source_chunk_id']) if item.get('source_chunk_id') else None
        if item.get('source_chunk_id') and (not chunk or chunk.course_id!=course_id): raise HTTPException(422,'计划来源不属于本课程')
    if any(p.get('concept_id') not in ids or p.get('prerequisite_id') not in ids or p.get('concept_id')==p.get('prerequisite_id') for p in body.prerequisites): raise HTTPException(422,'先修关系必须属于当前计划且不能指向自身')
    for link in db.scalars(select(m.CourseConcept).where(m.CourseConcept.course_id == course_id)).all(): db.delete(link)
    for link in db.scalars(select(m.Prerequisite).where(m.Prerequisite.course_id == course_id)).all(): db.delete(link)
    db.flush()
    for index, item in enumerate(body.concepts):
        cid = item['id']
        if not db.get(m.Concept, cid): db.add(m.Concept(id=cid, name=item['name']))
        db.flush()
        db.add(m.CourseConcept(course_id=course_id, concept_id=cid, position=index, source_chunk_id=item.get('source_chunk_id')))
    for item in body.prerequisites:
        db.add(m.Prerequisite(course_id=course_id, concept_id=item['concept_id'], prerequisite_id=item['prerequisite_id']))
    db.commit()
    return {'status': 'confirmed', 'count': len(body.concepts)}

class BackgroundInput(BaseModel):
    client_operation_id:str
    field:str='人工智能'
    directions:list[str]=Field(default_factory=lambda:['数学','概率统计'])
    starting:str='高中水平'

@app.get('/v1/background')
def background_state(db: Session = Depends(get_db)):
    return db.get(m.Profile,'local_user').settings

@app.post('/v1/background')
def background_setup(body: BackgroundInput, db: Session = Depends(get_db)):
    from .background import initialize
    return initialize(db,body.client_operation_id,body.field,body.directions,body.starting)

@app.get('/v1/library')
def library(q: str='', category: str='', subject: str='', direction: str='', mastery: int | None=Query(None,ge=0,le=5), page:int=Query(1,ge=1), page_size:int=Query(30,ge=1,le=100), db: Session = Depends(get_db)):
    from .library import rows
    return rows(db,q,category,subject,direction,mastery,page,page_size)

@app.get('/v1/library/taxonomy')
def library_taxonomy(db: Session = Depends(get_db)):
    from .classification import taxonomy
    return taxonomy(db)

@app.get('/v1/library/merge-options')
def merge_options(db: Session = Depends(get_db)):
    # Local compact catalog, independent of the current results page.
    return [{'concept_id':c.id,'name':c.name,'mastery_level':snapshot(db,c.id)['mastery_level']} for c in db.scalars(select(m.Concept)).all() if not (c.details or {}).get('redirect')]

@app.get('/v1/knowledge/{concept_id}/history')
def knowledge_history(concept_id: str, db: Session = Depends(get_db)):
    from .resolver import canonical
    target=canonical(db,concept_id)
    related=[c.id for c in db.scalars(select(m.Concept)).all() if canonical(db,c.id)==target]
    events=db.scalars(select(m.LearnerEvent).where(m.LearnerEvent.concept_id.in_(related)).order_by(text('rowid desc')).limit(30)).all()
    return [{'id':e.id,'kind':e.kind,'date':e.created_at,'reason':e.after.get('result',{}).get('reason',e.after.get('result',{}).get('last_reason',{'prerequisite_exposure':'接触到基础解释，不计完成','answer_exposure':'收到解答，不计完成','question_observed':'提问已记录','mastery_baseline':'旧画像迁移，未追加升级'}.get(e.kind,e.kind))),'revoked':e.after.get('_revoked',False),'correctable':e.kind in {'mastery_question','mastery_practice'}} for e in events]

@app.post('/v1/evidence/{event_id}/revoke')
def revoke_evidence(event_id: str, body: OperationInput, db: Session = Depends(get_db)):
    from .mastery import revoke
    return revoke(db,body.client_operation_id,event_id)

class MergeInput(BaseModel):
    source_id:str
    target_id:str
    level:int
    client_operation_id:str

@app.post('/v1/library/merge')
def merge_concepts(body: MergeInput, db: Session = Depends(get_db)):
    from .library import merge
    return merge(db,body.source_id,body.target_id,body.level,body.client_operation_id)

@app.get('/v1/knowledge')
def knowledge(db: Session = Depends(get_db)):
    return {'learner_revision': db.get(m.Profile, 'local_user').revision, 'states': [snapshot(db, c.id) for c in db.scalars(select(m.Concept)).all()]}

@app.post('/v1/knowledge/events')
def knowledge_event(body: KnowledgeInput, db: Session = Depends(get_db)):
    if body.mastery_level is not None:
        from .mastery import settle
        return settle(db,body.client_operation_id,body.concept_id,'manual',{'level':body.mastery_level},body.expected_version)
    return apply_event(db, body.client_operation_id, body.concept_id, body.familiarity, body.support_override, body.expected_version, body.reason)

def _execute_turn(body: TurnInput, db: Session = Depends(get_db)):
    existing = db.scalar(select(m.Turn).where(m.Turn.operation_id == body.client_operation_id))
    if existing and existing.status not in {'failed', 'interrupted', 'insufficient_evidence'}:
        if existing.action=='ask' and existing.result and existing.result.get('attribution_raw') and 'attribution' not in existing.result:
            from .questions import record_attribution
            a=record_attribution(db,body,existing,existing.result['attribution_raw']);existing.result={**existing.result,'attribution':a};db.commit()
        return {**existing.result,'cache_hit':True,'model_calls':0,'generation_usage':existing.result.get('usage',{}),'usage':{'input_tokens':0,'output_tokens':0,'cached_tokens':0}} if existing.result else {'turn_id': existing.id, 'status': existing.status, 'error': existing.error}
    if not demo_requested() and not configured():raise HTTPException(503,'真实模型模式尚未配置有效 API Key，请检查 backend/.env 后重启后端')
    if body.action not in {'teach', 'ask'}: raise HTTPException(422, 'Supported actions: teach, ask')
    if not db.get(m.CourseConcept, (body.course_id, body.concept_id)): raise HTTPException(404, 'Concept not in course')
    if body.action == 'ask':
        lesson = db.get(m.Lesson, body.lesson_id) if body.lesson_id else None
        if not lesson: raise HTTPException(404, 'Lesson not found')
        if body.selection:
            block = next((b for b in lesson.blocks if b['block_id'] == body.selection.get('block_id')), None)
            if not block: raise HTTPException(422, 'Invalid block selection')
            if body.selection.get('format') == 'rendered':
                selected_ids=body.selection.get('block_ids',[block['block_id']])
                hashes=body.selection.get('block_hashes',{})
                for bid in selected_ids:
                    source=next((b for b in lesson.blocks if b['block_id']==bid),None)
                    if not source or hashes.get(bid)!=hashlib.sha256(source['markdown'].encode()).hexdigest():raise HTTPException(422,'原文已变化，请重新选中')
                quote=body.selection.get('quote','')
                if not quote.strip() or len(quote)>6000:raise HTTPException(422,'请选择较短的原文')
            else:
                start, end = body.selection.get('start'), body.selection.get('end')
                if not isinstance(start,int) or not isinstance(end,int) or start<0 or end<start or block['markdown'][start:end]!=body.selection.get('quote'):
                    raise HTTPException(422,'Selection does not match the saved lesson block')
    analysis={'model_calls':0,'usage':{}}
    if body.action=='teach':
        from .dependencies import analyze
        analysis=analyze(db,body.course_id,body.concept_id)
    plan = make_plan(db, body.course_id, body.concept_id, body.explicit_support)
    if existing:
        record = existing
        record.status = 'pending'
        record.error = None
        record.result = None
        record.request = body.model_dump()
    else:
        record = m.Turn(id=str(uuid4()), operation_id=body.client_operation_id, action=body.action, course_id=body.course_id, concept_id=body.concept_id, lesson_id=body.lesson_id, thread_id=body.thread_id or str(uuid4()), request=body.model_dump(), status='pending')
        db.add(record)
    db.commit()
    started = time.monotonic()
    try:
        history = [];summary=''
        if body.action == 'ask' and body.thread_id:
            previous = db.scalars(select(m.Turn).where(m.Turn.thread_id == body.thread_id, m.Turn.status.in_(['completed', 'insufficient_evidence'])).order_by(text('rowid'))).all()[-4:]
            summary=next((x.result.get('thread_summary','') for x in reversed(previous) if x.result and x.result.get('thread_summary')), '')
            history = [{'question': x.request.get('message',''), 'answer': ' '.join(b['markdown'] for b in x.result.get('blocks', []))} for x in previous if x.result]
        graph_result = run_graph(db, {'course_id': body.course_id, 'concept_id': body.concept_id, 'action': body.action, 'message': body.message, 'history': history, 'thread_summary': summary, 'selection': body.selection, 'plan': plan})
        blocks = [{**block, 'block_id': f'{record.id}-b{i}'} for i, block in enumerate(graph_result['blocks'])]
        db.expire_all()
        stale = any(snapshot(db,p['concept_id'])['state_version']!=p['state_version'] for p in plan['prerequisite_support']) or snapshot(db,body.concept_id)['state_version']!=plan['target_state']['state_version']
        lesson_id = body.lesson_id
        success=bool(graph_result['citations'] and blocks and blocks[0].get('kind')!='notice')
        if body.action == 'teach' and success:
            version = db.scalar(select(func.count()).select_from(m.Lesson).where(m.Lesson.course_id == body.course_id, m.Lesson.concept_id == body.concept_id)) + 1
            lesson = m.Lesson(id=str(uuid4()), course_id=body.course_id, concept_id=body.concept_id, version=version, learner_version=plan['profile_version'], blocks=blocks, citations=graph_result['citations'], teaching_plan=plan)
            db.add(lesson); db.flush(); lesson_id = lesson.id; record.lesson_id = lesson.id
        result = {'turn_id': record.id, 'status': 'completed' if success else 'insufficient_evidence', 'stale_profile': stale, 'learner_version_used': plan['profile_version'], 'teaching_plan': plan, 'lesson_id': lesson_id, 'thread_id': record.thread_id, 'blocks': blocks, 'citations': graph_result['citations'], 'pending_profile_updates': [x.model_dump() if hasattr(x, 'model_dump') else x for x in graph_result.get('pending_profile_updates', [])], 'demo_mode': graph_result['demo'], 'trace': graph_result['trace'], 'attribution_raw':graph_result.get('attribution'), 'analysis':analysis, 'model_calls':(0 if graph_result['demo'] else 2)+analysis.get('model_calls',0), 'thread_summary':graph_result.get('thread_summary',''), 'usage': graph_result.get('usage', {}), 'model': model_name() if not graph_result['demo'] else 'demo', 'elapsed_seconds': round(time.monotonic()-started,3), 'instance_id': INSTANCE_ID}
        record.result = result; record.status = result['status']; record.trace = graph_result['trace']
        state = db.get(m.LearnerState, ('local_user', body.concept_id))
        if state is None:
            state = m.LearnerState(profile_id='local_user', concept_id=body.concept_id)
            db.add(state)
        now = datetime.now(timezone.utc).isoformat()
        if body.action == 'ask':
            state.last_question_at = now
            db.add(m.LearnerEvent(id=str(uuid4()), operation_id='question:'+body.client_operation_id, concept_id=body.concept_id, kind='question_observed', before=snapshot(db, body.concept_id), after=snapshot(db, body.concept_id), created_at=now))
        else:
            state.last_taught_at = now
        exposed={cid for b in blocks for cid in b.get('concept_ids',[]) if cid!=body.concept_id}
        for cid in exposed:
            db.add(m.LearnerEvent(id=str(uuid4()),operation_id='exposure:'+record.id+':'+cid,concept_id=cid,kind='prerequisite_exposure',before=snapshot(db,cid),after=snapshot(db,cid),created_at=now))
        if body.action=='ask' and graph_result['citations']:
            db.add(m.LearnerEvent(id=str(uuid4()),operation_id='answer:'+record.id,concept_id=body.concept_id,kind='answer_exposure',before=snapshot(db,body.concept_id),after=snapshot(db,body.concept_id),created_at=now))
        db.commit()
        if graph_result.get('matching'):
            from .matching import apply as apply_matches
            matching=graph_result['matching']
            updates=apply_matches(db,matching['decisions'],matching['mapping'])
            result={**result,'matching':{'metrics':matching['metrics'],'updates':updates}};record.result=result;db.commit()
        if body.action=='ask':
            from .questions import record_attribution
            attribution=record_attribution(db,body,record,graph_result.get('attribution'))
            result={**result,'attribution':attribution}
            record.result=result;db.commit()
        return result
    except Exception as exc:
        record.status = 'failed'; record.error = f"{type(exc).__name__}: status={getattr(exc, 'status_code', None)}, code={getattr(exc, 'code', None)}"; db.commit()
        raise HTTPException(502, {'request_id': record.id, 'error': record.error}) from exc

@app.post('/v1/turns')
def turn(body: TurnInput, db: Session = Depends(get_db)):
    from .locks import task_lock
    with task_lock('lesson:'+body.course_id+':'+body.concept_id if body.action=='teach' else 'thread:'+str(body.thread_id or body.client_operation_id)):
        db.rollback();db.expire_all()
        if body.action=='teach':
            cached=db.scalar(select(m.Lesson).where(m.Lesson.course_id==body.course_id,m.Lesson.concept_id==body.concept_id,func.json_array_length(m.Lesson.citations)>0).order_by(m.Lesson.version.desc()))
            if cached and cached.citations and cached.blocks and cached.blocks[0].get('kind')!='notice':
                origin=db.scalar(select(m.Turn).where(m.Turn.lesson_id==cached.id,m.Turn.action=='teach'))
                return {**(origin.result if origin and origin.result else get_lesson(cached.id,db)),'lesson_id':cached.id,'cache_hit':True,'model_calls':0,'generation_usage':origin.result.get('usage',{}) if origin and origin.result else {},'usage':{'input_tokens':0,'output_tokens':0,'cached_tokens':0},'status':'completed'}
        return _execute_turn(body,db)

@app.get('/v1/courses/{course_id}/concepts/{concept_id}/lesson')
def cached_lesson(course_id: str, concept_id: str, db: Session = Depends(get_db)):
    row=db.scalar(select(m.Lesson).where(m.Lesson.course_id==course_id,m.Lesson.concept_id==concept_id,func.json_array_length(m.Lesson.citations)>0).order_by(m.Lesson.version.desc()))
    return {'lesson':get_lesson(row.id,db) if row and row.citations and row.blocks and row.blocks[0].get('kind')!='notice' else None,'model_calls':0}

@app.post('/v1/courses/{course_id}/concepts/{concept_id}/lesson')
def ensure_lesson(course_id: str, concept_id: str, db: Session = Depends(get_db)):
    from .locks import task_lock
    with task_lock('lesson:'+course_id+':'+concept_id):
        db.rollback();db.expire_all()
        cached=cached_lesson(course_id,concept_id,db)['lesson']
        if cached:return {'lesson':cached,'cache_hit':True,'model_calls':0}
        result=_execute_turn(TurnInput(client_operation_id='topic-generation:'+course_id+':'+concept_id,action='teach',course_id=course_id,concept_id=concept_id),db)
        if result.get('status')!='completed' or not result.get('lesson_id'):raise HTTPException(502,'当前教学依据不足，请补充资料后重试')
        return {'lesson':{**result,'id':result.get('lesson_id')},'cache_hit':False,'model_calls':result.get('model_calls',0)}

@app.get('/v1/courses/{course_id}/concepts/{concept_id}/generation')
def generation_status(course_id:str,concept_id:str,db:Session=Depends(get_db)):
    from .generation import status
    return status(db,course_id,concept_id)

@app.post('/v1/courses/{course_id}/concepts/{concept_id}/generation')
def start_generation(course_id:str,concept_id:str,db:Session=Depends(get_db)):
    from .generation import request,launch
    result,needed=request(db,course_id,concept_id)
    if needed:launch(result['job_id'])
    return result

class RunInput(BaseModel):
    client_operation_id:str
    lesson_id:str
    review:bool=False

@app.post('/v1/runs')
def start_run(body: RunInput, db: Session = Depends(get_db)):
    db.rollback();db.execute(text('BEGIN IMMEDIATE'));db.expire_all()
    old=db.get(m.StudyRun,body.client_operation_id)
    if old:db.rollback();return {'run_id':old.id,'completed':old.completed}
    lesson=db.get(m.Lesson,body.lesson_id)
    if not lesson:raise HTTPException(404,'讲义不存在')
    if not lesson.citations or not lesson.blocks or lesson.blocks[0].get('kind')=='notice':raise HTTPException(422,'讲义尚未成功生成，不能记录完成')
    from .policy import local_date,now
    state=snapshot(db,lesson.concept_id)
    day=local_date(now())
    # A completed run today stays reusable across tabs/reloads, even if its first reward was capped.
    ids=[x.id for x in db.scalars(select(m.StudyRun).where(m.StudyRun.course_id==lesson.course_id,m.StudyRun.concept_id==lesson.concept_id,m.StudyRun.completed==True)).all()]
    today=db.scalar(select(m.LearnerEvent).where(m.LearnerEvent.concept_id==state['concept_id'],m.LearnerEvent.kind=='mastery_complete',m.LearnerEvent.after['day'].as_string()==day,m.LearnerEvent.after['_input']['identity'].as_string().in_(ids))) if ids else None
    if today:
        db.rollback();return {'run_id':today.after['_input']['identity'],'completed':True}
    active=db.scalar(select(m.StudyRun).where(m.StudyRun.course_id==lesson.course_id,m.StudyRun.concept_id==lesson.concept_id,m.StudyRun.completed==False))
    if active:db.rollback();return {'run_id':active.id,'completed':False}
    row=m.StudyRun(id=body.client_operation_id,lesson_id=lesson.id,course_id=lesson.course_id,concept_id=lesson.concept_id,review=state['first_completed'])
    db.add(row);db.commit();return {'run_id':row.id,'completed':False}

@app.post('/v1/runs/{run_id}/complete')
def complete_run(run_id: str, body: OperationInput, db: Session = Depends(get_db)):
    run=db.get(m.StudyRun,run_id)
    if not run:raise HTTPException(404,'学习轮次不存在')
    from .mastery import settle
    return settle(db,body.client_operation_id,run.concept_id,'complete',{},run_id=run_id)

@app.get('/v1/turns/{turn_id}')
def get_turn(turn_id: str, db: Session = Depends(get_db)):
    record = db.get(m.Turn, turn_id)
    if not record: raise HTTPException(404, 'Turn not found')
    return record.result or {'turn_id': record.id, 'status': record.status, 'error': record.error}

@app.post('/v1/notes')
def save_note(body: NoteInput, db: Session = Depends(get_db)):
    old = db.scalar(select(m.Note).where(m.Note.operation_id == body.client_operation_id))
    if old: return {'id': old.id, 'lesson_id': old.lesson_id}
    lesson = db.get(m.Lesson, body.lesson_id)
    if not lesson or body.block_id not in [b['block_id'] for b in lesson.blocks]: raise HTTPException(422, 'Invalid lesson block')
    note = m.Note(id=str(uuid4()), operation_id=body.client_operation_id, lesson_id=body.lesson_id, block_id=body.block_id, anchor=body.anchor, text=body.text)
    db.add(note); db.commit()
    return {'id': note.id, 'lesson_id': note.lesson_id}

@app.get('/v1/lessons/{lesson_id}/notes')
def notes(lesson_id: str, db: Session = Depends(get_db)):
    return [{'id': n.id, 'block_id': n.block_id, 'anchor': n.anchor, 'text': n.text} for n in db.scalars(select(m.Note).where(m.Note.lesson_id == lesson_id)).all()]

@app.get('/v1/lessons/{lesson_id}')
def get_lesson(lesson_id: str, db: Session = Depends(get_db)):
    lesson = db.get(m.Lesson, lesson_id)
    if not lesson: raise HTTPException(404, 'Lesson not found')
    origin = db.scalar(select(m.Turn).where(m.Turn.lesson_id == lesson.id, m.Turn.action == 'teach'))
    return {'id': lesson.id, 'course_id': lesson.course_id, 'concept_id': lesson.concept_id, 'version': lesson.version, 'blocks': lesson.blocks, 'citations': lesson.citations, 'teaching_plan': lesson.teaching_plan, 'thread_id': origin.thread_id if origin else None}

@app.put('/v1/courses/{course_id}/progress')
def progress(course_id: str, body: ProgressInput, db: Session = Depends(get_db)):
    row = db.get(m.Progress, course_id)
    if row and row.operation_id == body.client_operation_id: return {'status': 'saved'}
    if not row:
        row = m.Progress(course_id=course_id, concept_id=body.concept_id)
        db.add(row)
    row.concept_id = body.concept_id; row.lesson_id = body.lesson_id; row.block_id = body.block_id; row.offset = body.offset; row.operation_id = body.client_operation_id
    db.add(m.LearnerEvent(id=str(uuid4()), operation_id='progress:'+body.client_operation_id, concept_id=body.concept_id, kind='progress_observed', before=snapshot(db, body.concept_id), after={**snapshot(db, body.concept_id),'course_id':course_id}, created_at=datetime.now(timezone.utc).isoformat()))
    db.commit()
    return {'status': 'saved'}

@app.get('/v1/courses/{course_id}/resume')
def resume(course_id: str, db: Session = Depends(get_db)):
    row = db.get(m.Progress, course_id)
    if not row: return {'progress': None, 'lesson': None, 'notes': []}
    lesson = db.get(m.Lesson, row.lesson_id) if row.lesson_id else None
    return {'progress': {'concept_id': row.concept_id, 'lesson_id': row.lesson_id, 'block_id': row.block_id, 'offset': row.offset}, 'lesson': get_lesson(lesson.id, db) if lesson else None, 'notes': notes(lesson.id, db) if lesson else []}

@app.post('/v1/courses/{course_id}/concepts/{concept_id}/exercise')
def new_exercise(course_id: str, concept_id: str, body: OperationInput, db: Session = Depends(get_db)):
    from .practice import generate
    try:return generate(db,course_id,concept_id,body.client_operation_id,body.new_question)
    except Exception as exc:
        db.rollback()
        raise HTTPException(502,'练习生成失败：'+type(exc).__name__) from exc

@app.get('/v1/concepts/{concept_id}/exercise')
def exercise(concept_id: str, db: Session = Depends(get_db)):
    item = db.scalar(select(m.Exercise).where(m.Exercise.concept_id == concept_id))
    if not item: raise HTTPException(404, 'No fixed exercise for this concept')
    return {'id': item.id, 'question': item.question, 'hint': item.hint, 'source': 'project-authored fixed demo exercise'}

@app.post('/v1/exercises/{exercise_id}/attempts')
def attempt(exercise_id: str, body: AttemptInput, db: Session = Depends(get_db)):
    from .practice import grade
    try:return grade(db,exercise_id,body.client_operation_id,body.response,body.hint_used)
    except Exception as exc:
        db.rollback();raise HTTPException(502,'评分失败，可重试：'+type(exc).__name__) from exc

@app.post('/v1/exercises/{exercise_id}/reveal/{field}')
def reveal_exercise(exercise_id: str, field: str, db: Session = Depends(get_db)):
    if field not in {'hint','answer'}:raise HTTPException(422,'Unsupported field')
    from .practice import reveal
    return reveal(db,exercise_id,field)
