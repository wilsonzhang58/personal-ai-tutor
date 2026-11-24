"""Tiny optional disambiguation payload carried by an existing teaching request."""
import json
from sqlalchemy import select
from . import models as m
from .resolver import alias,canonical

def payload(db,ids):
    compact=[];mapping={}
    for cid in ids:
        c=db.get(m.Concept,cid);details=c.details or {}
        if not details.get('provisional') or details.get('redirect'):continue
        candidates=details.get('candidates',[])[:3]
        item=[f'x{len(compact)}',c.name[:20],[]]
        local={}
        for i,candidate in enumerate(candidates):
            key='c'+str(i);item[2].append([key,candidate['name'][:18],candidate.get('scope','')[:24]]);local[key]=candidate['id']
        trial=compact+[item]
        # UTF-8 byte count bounds a byte-tokenizer payload conservatively; not a token measurement.
        if len(json.dumps(trial,ensure_ascii=False,separators=(',',':')).encode('utf-8'))+24>600:continue
        compact=trial;mapping[item[0]]={'source':cid,'candidates':local}
        if len(compact)==3:break
    return compact,mapping,{'concept_count':len(compact),'input_tokens_estimated':len(json.dumps(compact,ensure_ascii=False,separators=(',',':')).encode('utf-8'))+24 if compact else 0,'estimator':'conservative_utf8_bytes_not_tokenizer','extra_llm_calls':0}

def apply(db,decisions,mapping):
    updates=[]
    for decision in decisions[:3]:
        entry=mapping.get(decision.get('query'));target=entry['candidates'].get(decision.get('candidate')) if entry else None
        if not target or decision.get('relation')!='same':continue
        target=canonical(db,target)
        if target==entry['source']:continue
        source=db.get(m.Concept,entry['source']);destination=db.get(m.Concept,target)
        if not source or not destination:continue
        state=db.get(m.LearnerState,('local_user',source.id))
        evidence=db.scalar(select(m.LearnerEvent).where(m.LearnerEvent.concept_id==source.id,m.LearnerEvent.kind.notin_(['prerequisite_exposure','answer_exposure'])))
        if evidence or (state and (state.mastery_level>0 or state.mastery_data.get('first_completed'))):
            updates.append({'source':source.id,'status':'manual_conflict'});continue
        source.details={**source.details,'redirect':target}
        for a in db.scalars(select(m.ConceptAlias).where(m.ConceptAlias.concept_id==source.id)).all():alias(db,target,a.alias,a.context,'confirmed');db.delete(a)
        for cache in db.scalars(select(m.MatchCache)).all():
            if cache.data.get('concept_id')==source.id:db.delete(cache)
        updates.append({'source':source.id,'target':target,'status':'applies_to_future_requests_only'})
    if updates:db.get(m.Profile,'local_user').revision+=1
    db.commit();return updates
