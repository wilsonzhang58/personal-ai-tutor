import json
from pydantic import BaseModel
from sqlalchemy import select,text
from . import models as m
from .llm import client,model_name

class ReorderOutput(BaseModel):
    topic_ids:list[str]

def reorder(db,course_id):
    course=db.get(m.Course,course_id);version=course.route_version
    links=db.scalars(select(m.CourseConcept).where(m.CourseConcept.course_id==course_id).order_by(m.CourseConcept.position)).all()
    for x in links:
        if not x.topic_id:
            from uuid import uuid4
            x.topic_id=str(uuid4())
    db.commit()
    def fixed_ids():
        completed={r.concept_id for r in db.scalars(select(m.StudyRun).where(m.StudyRun.course_id==course_id,m.StudyRun.completed==True)).all()}
        completed.update(e.concept_id for e in db.scalars(select(m.LearnerEvent).where(m.LearnerEvent.kind=='concept_completed')).all() if e.after.get('course_id')==course_id)
        current=db.get(m.Progress,course_id)
        if current:completed.add(current.concept_id)
        return {x.topic_id for x in links if x.concept_id in completed}
    fixed=fixed_ids();expected_ids={x.topic_id for x in links}
    response=client().responses.parse(model=model_name(),instructions='重排尚未完成课程主题。只返回给定topic_id的完整排列，不得丢失、重复、添加ID。保持必要先修顺序，已完成和当前项由应用固定。',input=json.dumps({'topics':[{'id':x.topic_id,'name':db.get(m.Concept,x.concept_id).name,'fixed':x.topic_id in fixed} for x in links]},ensure_ascii=False),text_format=ReorderOutput,max_output_tokens=2200)
    output=response.output_parsed;db.rollback();db.execute(text('BEGIN IMMEDIATE'));db.expire_all()
    fresh=db.scalars(select(m.CourseConcept).where(m.CourseConcept.course_id==course_id)).all()
    if {x.topic_id for x in fresh}!=expected_ids:
        db.rollback();return {'status':'safe_append','reason':'路线已经变化，保留最新路线'}
    if not output or len(output.topic_ids)!=len(links) or set(output.topic_ids)!={x.topic_id for x in links}:
        db.rollback();return {'status':'safe_append','reason':'重排 ID 无效，保留原顺序'}
    if db.get(m.Course,course_id).route_version!=version or fixed_ids()!=fixed:
        db.rollback();return {'status':'safe_append','reason':'学习进度已变化，保留安全追加顺序'}
    movable=iter(x for x in output.topic_ids if x not in fixed)
    order=[x.topic_id if x.topic_id in fixed else next(movable) for x in links]
    positions={x:i for i,x in enumerate(order)};by_concept={x.concept_id:x.topic_id for x in links}
    for dep in db.scalars(select(m.Prerequisite).where(m.Prerequisite.course_id==course_id)).all():
        if dep.prerequisite_id in by_concept and positions[by_concept[dep.prerequisite_id]]>=positions[by_concept[dep.concept_id]]:
            db.rollback();return {'status':'safe_append','reason':'先修顺序冲突，保留安全追加顺序'}
    for x in links:x.position=positions[x.topic_id]
    db.get(m.Course,course_id).route_version+=1;db.commit()
    return {'status':'reordered','usage':response.usage.model_dump() if response.usage else {}}
