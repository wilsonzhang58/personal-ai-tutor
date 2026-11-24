"""Generate candidates from confirmed source chunks; confirmation remains a transaction."""
import json
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy import select
from fastapi import HTTPException
from . import models as m
from .llm import client, configured, model_name

class Candidate(BaseModel):
    id: str
    name: str
    definition: str = ''
    source_chunk_id: str

class Dependency(BaseModel):
    concept_id: str
    prerequisite_id: str

class CoursePlan(BaseModel):
    concepts: list[Candidate]
    prerequisites: list[Dependency]

def extract_plan(db, course_id):
    rows=db.scalars(select(m.SourceChunk).join(m.Material,m.SourceChunk.material_id==m.Material.id).where(m.SourceChunk.course_id==course_id,m.Material.confirmed==True,m.SourceChunk.material_version==m.Material.version)).all()
    if not rows: raise HTTPException(422,'请先上传、检查并确认课件文本')
    if not configured(): raise HTTPException(409,'生成候选计划需要真实模型配置')
    # Bound context while sampling every material in page order.
    by_material={}
    for r in rows: by_material.setdefault(r.material_id,[]).append(r)
    selected=[]
    for group in by_material.values():
        group=sorted(group,key=lambda r:r.page)
        stride=max(1,len(group)//12)
        selected.extend(group[::stride][:12])
    sources=[{'id':r.id,'page':r.page,'text':r.text[:900]} for r in selected]
    known=[{'id':c.id,'name':c.name} for c in db.scalars(select(m.Concept)).all()]
    prompt=(Path(__file__).parent/'prompts/extract_course_plan.md').read_text()
    response=client().responses.parse(model=model_name(),instructions=prompt+' Propose 5-12 teachable concepts. Use exact known IDs only for the same concept; otherwise use stable descriptive IDs. Each concept needs a supplied source_chunk_id. Prerequisites must reference concepts in this proposal. Return no more than two direct prerequisites per target.',input=json.dumps({'sources':sources,'known_concepts':known},ensure_ascii=False),text_format=CoursePlan)
    plan=response.output_parsed
    if plan is None: raise ValueError('No structured course plan returned')
    ids={r.id for r in selected}; concept_ids={c.id for c in plan.concepts}
    if len(concept_ids)!=len(plan.concepts) or any(c.source_chunk_id not in ids for c in plan.concepts): raise ValueError('Invalid candidate source or duplicate concept')
    if any(d.concept_id not in concept_ids or d.prerequisite_id not in concept_ids or d.concept_id==d.prerequisite_id for d in plan.prerequisites): raise ValueError('Invalid prerequisite')
    return {**plan.model_dump(),'status':'pending_confirmation','source':'openai','model':model_name(),'usage':response.usage.model_dump() if response.usage else {}}
