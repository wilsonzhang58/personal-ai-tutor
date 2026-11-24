from . import models as m
from .resolver import resolve
from .mastery import settle
from .knowledge import snapshot

def record_attribution(db,body,turn,attribution):
    if not attribution:return {'status':'unclassified','reason':'没有可靠归因，只记录提问'}
    cid=attribution.get('primary_concept');known=[body.concept_id]+[p['concept_id'] for p in turn.result['teaching_plan']['prerequisite_support']]
    resolved=bool(cid in known and attribution.get('certain'))
    if not resolved and attribution.get('name'):
        match=resolve(db,name=attribution['name'],context=body.course_id,create=False,allow_embedding=False)
        cid=match['concept_id'];resolved=bool(cid and match['status']=='same' and attribution.get('certain'))
    if not cid and attribution.get('clear_basic') and attribution.get('intent')=='basic_difficulty' and 2<=len(attribution.get('name',''))<=80:
        match=resolve(db,name=attribution['name'],context=body.course_id);cid=match['concept_id'];resolved=False;db.commit()
    if not cid:
        return {**attribution,'status':'uncertain','reason':'归因待确认，不调整等级'}
    if any(u.get('source')==cid for u in turn.result.get('matching',{}).get('updates',[])):resolved=False
    payload={'identity':turn.thread_id,'clear_basic':attribution.get('clear_basic',False),'intent':attribution.get('intent'),'resolved':resolved,'is_target':snapshot(db,cid)['concept_id']==snapshot(db,body.concept_id)['concept_id']}
    used=next((p['state_version'] for p in turn.result['teaching_plan']['prerequisite_support'] if p['concept_id']==cid),None)
    payload['state_version_used']=used
    result=settle(db,'attribution:'+turn.id,cid,'question',payload)
    # A successful explanation is exposure only, never a completion/review reward.
    return {**attribution,'concept_id':cid,'episode_id':turn.thread_id+':'+cid,'result':result,'status':'recorded' if resolved else 'uncertain'}
