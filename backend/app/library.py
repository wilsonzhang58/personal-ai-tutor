from uuid import uuid4
from sqlalchemy import select
from fastapi import HTTPException
from . import models as m
from .knowledge import snapshot
from .mastery import settle,revoke
from .resolver import normalize,alias,canonical

def rows(db,query='',category='',subject='',direction='',mastery=None,page=1,page_size=30):
    from sqlalchemy import func,or_,exists
    from .classification import ensure
    ensure(db)
    level=func.coalesce(m.LearnerState.mastery_level,0)
    stmt=select(m.Concept).outerjoin(m.LearnerState,(m.LearnerState.concept_id==m.Concept.id)&(m.LearnerState.profile_id=='local_user')).where(m.Concept.details['redirect'].as_string().is_(None))
    if subject:stmt=stmt.where(m.Concept.details['subject'].as_string()==subject)
    if direction:stmt=stmt.where(m.Concept.details['direction'].as_string()==direction)
    if category:
        parts=category.split('/',1)
        stmt=stmt.where(m.Concept.details['subject'].as_string()==parts[0])
        if len(parts)>1:stmt=stmt.where(m.Concept.details['direction'].as_string()==parts[1])
    if mastery is not None:stmt=stmt.where(level==mastery)
    if query.strip():
        term=normalize(query.strip())
        # SQL filtering precedes pagination; aliases are an EXISTS, not a limited client list.
        stmt=stmt.where(or_(func.lower(m.Concept.name).contains(term,autoescape=True),
            func.lower(m.Concept.details['english_name'].as_string()).contains(term,autoescape=True),
            exists(select(m.ConceptAlias.alias).where(m.ConceptAlias.concept_id==m.Concept.id,func.lower(m.ConceptAlias.alias).contains(term,autoescape=True)))))
    total=db.scalar(select(func.count()).select_from(stmt.subquery()))
    selected=db.scalars(stmt.order_by(m.Concept.name,m.Concept.id).offset((page-1)*page_size).limit(page_size)).all()
    all_concepts=db.scalars(select(m.Concept)).all()
    result=[]
    for c in selected:
        related=[x.id for x in all_concepts if canonical(db,x.id)==c.id]
        links=db.scalars(select(m.CourseConcept).where(m.CourseConcept.concept_id.in_(related))).all()
        prerequisites=db.scalars(select(m.Prerequisite).where(m.Prerequisite.prerequisite_id.in_(related))).all()
        courses=sorted({db.get(m.Course,x.course_id).title for x in [*links,*prerequisites]})
        state=snapshot(db,c.id);d=c.details or {}
        result.append({'concept_id':c.id,'name':c.name,'english_name':d.get('english_name',''),'subject':d['subject'],'direction':d['direction'],'mastery_level':state['mastery_level'],'state_version':state['state_version'],'courses':courses})
    return {'items':result,'total':total,'page':page,'page_size':page_size,'model_calls':0,'usage':{'input_tokens':0,'output_tokens':0}}

def merge(db,source_id,target_id,level,operation_id):
    existing=db.scalar(select(m.LearnerEvent).where(m.LearnerEvent.operation_id==operation_id))
    if existing:return existing.after.get('result',existing.after)
    source=db.get(m.Concept,source_id);target=db.get(m.Concept,target_id)
    if not source or not target or source_id==target_id:raise HTTPException(422,'请选择两个不同的有效概念')
    if canonical(db,target_id)==source_id or (source.details or {}).get('redirect'):raise HTTPException(422,'不能重复或循环合并')
    target_id=canonical(db,target_id)
    if not 0<=level<=5:raise HTTPException(422,'请明确选择合并后等级')
    old=db.get(m.LearnerState,('local_user',source_id));target_state=db.get(m.LearnerState,('local_user',target_id))
    old_data=old.mastery_data if old else {};target_data=target_state.mastery_data if target_state else {};dates={}
    for key in ('first_date','last_up','last_down','review_date'):
        values=[v for v in [old_data.get(key),target_data.get(key)] if v]
        if values:dates[key]=min(values) if key=='first_date' else max(values)
    def patch(session):
        row=session.get(m.Concept,source_id);row.details={**(row.details or {}),'redirect':target_id}
        for a in session.scalars(select(m.ConceptAlias).where(m.ConceptAlias.concept_id==source_id)).all():
            alias(session,target_id,a.alias,a.context,'confirmed');session.delete(a)
        for cache in session.scalars(select(m.MatchCache)).all():
            if cache.data.get('concept_id') in {source_id,target_id}:session.delete(cache)
    return settle(db,operation_id,target_id,'manual',{'level':level,'source':'manual_merge','merge_source':source_id,'merge_target':target_id,'first_completed':bool(old_data.get('first_completed')),**dates},before_commit=patch)
