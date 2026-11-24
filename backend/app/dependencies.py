import hashlib,json
from pydantic import BaseModel
from sqlalchemy import select
from . import models as m
from .resolver import resolve_batch
from .llm import client,configured,model_name

class RequiredConcept(BaseModel):
    name:str
    definition:str
    necessity:str
    location:str

class DependencyOutput(BaseModel):
    prerequisites:list[RequiredConcept]

def analyze(db,course_id,concept_id):
    link=db.get(m.CourseConcept,(course_id,concept_id))
    if not link:return {'model_calls':0,'usage':{}}
    existing=db.scalars(select(m.Prerequisite).where(m.Prerequisite.course_id==course_id,m.Prerequisite.concept_id==concept_id)).all()
    # Trust authored/imported direct links; annotate scope instead of paying to rediscover them.
    if existing:
        for row in existing:
            if not row.details:row.details={'necessity':'已确认课程内直接依赖','location':'本主题相关计算步骤','analysis_version':'existing-v1'}
        db.commit();return {'model_calls':0,'usage':{}}
    source=db.get(m.SourceChunk,link.source_chunk_id) if link.source_chunk_id else None
    if not source or not configured():return {'model_calls':0,'usage':{}}
    key=hashlib.sha256((course_id+concept_id+source.id+source.text+'dependency-v1').encode()).hexdigest()
    if db.get(m.AnalysisCache,key):return {'model_calls':0,'usage':{},'cache_hit':True}
    result=client().responses.parse(model=model_name(),instructions='分析本教学单元直接且必要的先修知识。只查一层，允许0项，通常2–5项，最多6项，确实必要最多10项；不能凑数或铺开整门数学。每项一句定义、必要性、将在何处使用。不要判定用户掌握程度。',input=json.dumps({'target':db.get(m.Concept,concept_id).name,'scope':link.scope,'source':source.text[:2400]},ensure_ascii=False),text_format=DependencyOutput,max_output_tokens=1800)
    output=result.output_parsed
    if output is None:raise ValueError('前置分析未完成')
    if len(output.prerequisites)>10:raise ValueError('主题需要超过10项基础，请拆分教学范围')
    resolved=resolve_batch(db,[{'name':p.name,'definition':p.definition} for p in output.prerequisites[:10]],context=course_id)
    seen=set()
    for p,r in zip(output.prerequisites,resolved):
        if r['concept_id']==concept_id or r['concept_id'] in seen:continue
        seen.add(r['concept_id'])
        db.add(m.Prerequisite(course_id=course_id,concept_id=concept_id,prerequisite_id=r['concept_id'],details={'necessity':p.necessity,'location':p.location,'definition':p.definition,'analysis_version':'v1','matching':r}))
    db.add(m.AnalysisCache(key=key,data={'usage':result.usage.model_dump() if result.usage else {},'model':model_name(),'scope':source.id}));db.commit()
    return {'model_calls':1,'usage':result.usage.model_dump() if result.usage else {},'matching':resolved}
