from datetime import datetime
from uuid import uuid4
from sqlalchemy import select,text
from fastapi import HTTPException
from . import models as m
from .db import SessionLocal
from . import policy

FIELDS=['mastery_level','mastery_data','state_version','evidence_source']
def state_for(db,cid):
    state=db.get(m.LearnerState,('local_user',cid))
    if state is None:
        state=m.LearnerState(profile_id='local_user',concept_id=cid,mastery_level=0,mastery_data=policy.empty(),state_version=0,familiarity='unknown',support_override='auto',evidence_source='unknown')
        db.add(state);db.flush()
    return state

def view(state):
    d=state.mastery_data or policy.empty()
    return {'concept_id':state.concept_id,'mastery_level':state.mastery_level,'label':policy.LABELS[state.mastery_level],'review_progress':d.get('review_count',0),'first_completed':d.get('first_completed',False),'evidence_source':state.evidence_source,'state_version':state.state_version,'last_reason':d.get('reason','尚无学习记录'),'familiarity':state.familiarity,'support_override':state.support_override}

def settle(db,operation_id,cid,kind,payload,expected_version=None,moment=None,run_id=None,before_commit=None):
    # SQLite's reserved write lock serializes all concepts across threads/processes.
    db.rollback();db.execute(text('BEGIN IMMEDIATE'));db.expire_all()
    old=db.scalar(select(m.LearnerEvent).where(m.LearnerEvent.operation_id==operation_id))
    if old:
        db.rollback();return old.after.get('result',old.after)
    if not db.get(m.Concept,cid):db.rollback();raise HTTPException(404,'概念不存在')
    concept=db.get(m.Concept,cid)
    from .resolver import canonical
    cid=canonical(db,cid)
    state=state_for(db,cid)
    if expected_version is not None and expected_version!=state.state_version:
        db.rollback();raise HTTPException(409,'知识状态已更新，请刷新后重试')
    run=db.get(m.StudyRun,run_id) if run_id else None
    if run_id and (not run or run.concept_id not in {cid,concept.id}):db.rollback();raise HTTPException(422,'学习轮次不匹配')
    if run and run.completed:
        result={**view(state),'reason':'该学习轮次已记录，不重复计数'}
        db.rollback();return result
    if run:payload={**payload,'identity':run.id}
    identity=payload.get('identity')
    if identity:
        duplicate=db.scalar(select(m.LearnerEvent).where(m.LearnerEvent.concept_id==cid,m.LearnerEvent.kind=='mastery_'+kind,m.LearnerEvent.after['_input']['identity'].as_string()==str(identity)))
        if duplicate:
            result={**view(state),'event_id':duplicate.id,'reason':'该学习轮次或证据已记录，不重复计数'}
            db.rollback();return result
    if kind=='manual' and (not isinstance(payload.get('level'),int) or not 0<=payload['level']<=5):db.rollback();raise HTTPException(422,'掌握程度需为 0–5')
    if payload.get('state_version_used') is not None and payload['state_version_used']!=state.state_version:
        payload={**payload,'resolved':False,'reliable':False,'stale_evidence':True}
    before=view(state);at=moment or policy.now();day=policy.local_date(at)
    level,data,reason=policy.reduce(state.mastery_level,state.mastery_data or policy.empty(),kind,payload,day)
    data['reason']=reason;state.mastery_level=level;state.mastery_data=data;state.evidence_source=data.get('source','unknown');state.state_version+=1
    state.familiarity='unknown' if level==0 else 'needs_review' if level<=2 else 'self_reported_familiar';state.support_override='auto'
    if run:run.completed=True
    profile=db.get(m.Profile,'local_user');profile.revision+=1
    event_id=str(uuid4());state.last_event_id=event_id
    result={**view(state),'event_id':event_id,'reason':reason}
    db.add(m.LearnerEvent(id=event_id,operation_id=operation_id,concept_id=cid,kind='mastery_'+kind,before=before,after={'_input':payload,'day':day,'policy':policy.VERSION,'result':result},created_at=at.isoformat()))
    if before_commit:before_commit(db)
    db.commit();return result

def revoke(db,operation_id,event_id):
    db.rollback();db.execute(text('BEGIN IMMEDIATE'));db.expire_all()
    old=db.scalar(select(m.LearnerEvent).where(m.LearnerEvent.operation_id==operation_id))
    if old:db.rollback();return old.after['result']
    target=db.get(m.LearnerEvent,event_id)
    if not target or target.kind not in {'mastery_question','mastery_practice'}:db.rollback();raise HTTPException(422,'只能纠正问题归因或练习评分')
    target.after={**target.after,'_revoked':True}
    from .resolver import canonical
    cid=canonical(db,target.concept_id)
    ids=[c.id for c in db.scalars(select(m.Concept)).all() if canonical(db,c.id)==cid]
    events=db.scalars(select(m.LearnerEvent).where(m.LearnerEvent.concept_id.in_(ids)).order_by(text('rowid'))).all()
    level=0;data=policy.empty()
    for event in events:
        if event.kind=='mastery_baseline':level=event.after['level'];data=event.after['data']
        elif event.after.get('_revoked') and event.after.get('_input',{}).get('identity'):
            data={**data,'seen':list(data.get('seen',[]))+[event.kind.removeprefix('mastery_')+':'+event.after['_input']['identity']]}
        elif event.kind.startswith('mastery_') and '_input' in event.after and not event.after.get('_revoked'):
            level,data,_=policy.reduce(level,data,event.kind.removeprefix('mastery_'),event.after['_input'],event.after['day'])
    state=state_for(db,cid);before=view(state);state.mastery_level=level;state.mastery_data={**data,'reason':'已撤销错误证据并按有效记录重算'};state.evidence_source=data.get('source','unknown');state.state_version+=1
    state.familiarity='unknown' if level==0 else 'needs_review' if level<=2 else 'self_reported_familiar'
    db.get(m.Profile,'local_user').revision+=1;result=view(state)
    db.add(m.LearnerEvent(id=str(uuid4()),operation_id=operation_id,concept_id=cid,kind='mastery_correction',before=before,after={'target_event':event_id,'result':result},created_at=policy.now().isoformat()))
    db.commit();return result
