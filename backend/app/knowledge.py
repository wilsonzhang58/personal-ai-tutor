from .models import LearnerState,Concept
from .mastery import settle,view
from . import policy

def snapshot(db,concept_id):
    requested=concept_id
    from .resolver import canonical
    concept_id=canonical(db,concept_id)
    state=db.get(LearnerState,('local_user',concept_id))
    if state:return {**view(state),'requested_concept_id':requested}
    return {'concept_id':concept_id,'mastery_level':0,'label':policy.LABELS[0],'review_progress':0,'first_completed':False,'evidence_source':'unknown','state_version':0,'familiarity':'unknown','support_override':'auto','last_reason':'尚无学习记录'}

def apply_event(db,operation_id,concept_id,familiarity=None,support_override=None,expected_version=None,reason='self_report'):
    # Legacy API remains readable, but support depth no longer sets mastery.
    level={'unknown':0,'needs_review':1,'self_reported_familiar':3}.get(familiarity)
    if level is None:level=snapshot(db,concept_id)['mastery_level']
    return settle(db,operation_id,concept_id,'manual',{'level':level,'source':'manual_self_report'},expected_version)
