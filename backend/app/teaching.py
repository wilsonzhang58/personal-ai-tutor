from sqlalchemy import select
from .models import Prerequisite, Profile
from .knowledge import snapshot
from .policy import LABELS
DEPTH={0:'detailed',1:'detailed',2:'standard',3:'brief',4:'reminder',5:'none'}
GUIDANCE={0:'必要定义、直观、基础例子与当前用途',1:'关键定义、步骤、基础例子与用途',2:'当前用到的定义步骤与短例子',3:'简短回顾关键公式、条件及当前作用',4:'一句提醒，保留符号和适用条件',5:'不专门补基础，正常使用并定义本节新符号'}

def depth_for(state,explicit=None):
    if explicit in {'brief','standard','detailed'}:return explicit,'explicit_current_request'
    level=state.get('mastery_level',0)
    return DEPTH[level],'mastery_level_'+str(level)

def make_plan(db,course_id,concept_id,explicit=None):
    rows=db.scalars(select(Prerequisite).where(Prerequisite.course_id==course_id,Prerequisite.concept_id==concept_id).order_by(Prerequisite.prerequisite_id)).all()
    if len(rows)>10:
        from fastapi import HTTPException
        raise HTTPException(422,'本主题直接前置超过10项，请拆分教学范围')
    support=[]
    for row in rows:
        state=snapshot(db,row.prerequisite_id);depth,reason=depth_for(state,explicit)
        support.append({'concept_id':row.prerequisite_id,'mastery_level':state['mastery_level'],'depth':depth,'guidance':GUIDANCE[state['mastery_level']],'reason':reason,'state_version':state['state_version'],'necessity':{k:row.details.get(k,'') for k in ('necessity','location','definition')}})
    return {'target_concept_id':concept_id,'profile_version':db.get(Profile,'local_user').revision,'prerequisite_support':support,'max_prerequisite_blocks':len(support),'response_language':'zh-CN','target_state':snapshot(db,concept_id),'starting_background':(db.get(Profile,'local_user').settings or {}).get('starting','高中毕业基础'),'placement':'在实际用到的位置补充，不能删除目标教学内容'}
