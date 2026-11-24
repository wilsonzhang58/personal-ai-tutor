import json
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from sqlalchemy import select
from .models import CourseConcept, Concept, Material, SourceChunk, TopicSource
from .tools import search_course_materials, read_source_chunk
from .llm import configured, generate_with_tools

class GraphState(TypedDict, total=False):
    course_id: str
    concept_id: str
    action: str
    message: str
    history: list
    selection: dict
    plan: dict
    source: list
    blocks: list
    trace: list
    citations: list
    pending_profile_updates: list
    usage: dict
    demo: bool
    attribution: dict
    thread_summary: str
    matching: dict

def allowed_courses(db, course_id, concept_id, plan):
    ids = {course_id}
    for item in plan['prerequisite_support']:
        links = db.scalars(select(CourseConcept).where(CourseConcept.concept_id == item['concept_id'])).all()
        ids.update(link.course_id for link in links)
    return sorted(ids)

def model_plan(plan):
    # Keep instructional decisions; omit ledger counters, provenance and versions.
    keys=('target_concept_id','response_language','starting_background','placement')
    return {**{k:plan[k] for k in keys if k in plan},
            'target_mastery_level':plan['target_state']['mastery_level'],
            'prerequisite_support':[{k:p[k] for k in ('concept_id','mastery_level','depth','guidance','necessity') if k in p} for p in plan['prerequisite_support']]}

def run_graph(db, state):
    def search_with_anchors(s, query):
        rows = search_course_materials(db, query, s['course_id'], allowed_courses(db, s['course_id'], s['concept_id'], s['plan']))
        concept_ids = [s['concept_id']] + [p['concept_id'] for p in s['plan']['prerequisite_support']]
        anchors = []
        for cid in concept_ids:
            links = db.scalars(select(CourseConcept).where(CourseConcept.concept_id == cid)).all()
            for link in links:
                if link.course_id not in allowed_courses(db,s['course_id'],s['concept_id'],s['plan']): continue
                supplemental=db.scalars(select(TopicSource).where(TopicSource.topic_id==link.topic_id)).all() if link.topic_id else []
                row_ids=[link.source_chunk_id]+[s.chunk_id for s in supplemental]
                for row_id in row_ids:
                    row=db.get(SourceChunk,row_id) if row_id else None
                    if row:
                        material=db.get(Material,row.material_id)
                        if material.confirmed and row.material_version==material.version:
                            anchors.append({'id':row.id,'course_id':row.course_id,'material_id':row.material_id,'page':row.page,'text':row.text,'score':1.0})
        return list({r['id']:r for r in [*anchors,*rows]}.values())
    def retrieve(s):
        concept = db.get(Concept, s['concept_id'])
        query = f"{concept.name} {s.get('message', '')}"
        allowed = allowed_courses(db, s['course_id'], s['concept_id'], s['plan'])
        source = search_with_anchors(s, query)
        trace = [{'tool': 'search_course_materials', 'query': query, 'allowed_courses': allowed, 'result_ids': [r['id'] for r in source]}]
        if source:
            chunk = read_source_chunk(db, source[0]['id'], {r['id'] for r in source})
            trace.append({'tool': 'read_source_chunk', 'chunk_id': chunk['id']})
        return {'source': source, 'trace': trace}
    def compose(s):
        source = s['source']
        def citations_for(rows):
            citations = []
            for row in rows:
                material = db.get(Material, row['material_id'])
                citations.append({'id': row['id'], 'material_id': row['material_id'], 'title': material.title, 'page': row['page'], 'excerpt': row['text'][:220]})
            return citations
        citations = citations_for(source)
        if not source:
            return {'blocks': [{'kind': 'notice', 'markdown': '当前资料不足，请导入或修正课件后重试。', 'citation_ids': []}], 'citations': [], 'demo': not configured(), 'pending_profile_updates': []}
        if configured():
            prompt_file = 'teach.md' if s['action'] == 'teach' else 'answer_selection.md'
            prompt = (Path(__file__).parent / 'prompts' / prompt_file).read_text()
            authorized_ids = {r['id'] for r in source}
            def handle_tool(name, args):
                if name == 'search_course_materials':
                    rows = search_with_anchors(s, args['query'])
                    authorized_ids.update(r['id'] for r in rows)
                    return rows
                if name == 'read_source_chunk':
                    return read_source_chunk(db, args['chunk_id'], authorized_ids)
                raise ValueError('Unknown tool')
            from .matching import payload
            ambiguous,mapping,matching_metrics=payload(db,[p['concept_id'] for p in s['plan']['prerequisite_support']])
            if ambiguous:prompt+=' For ambiguous_matches only, return matches as query/candidate aliases and relation same/related/different/uncertain. same requires identical scope, not merely related. Maximum 3 compact decisions, under 200 output tokens. Do not change the teaching plan already read.'
            output, usage, model_trace, tool_sources = generate_with_tools(prompt, json.dumps({'plan': model_plan(s['plan']), 'action': s['action'], 'message': s.get('message'), 'history': s.get('history', []), 'thread_summary': s.get('thread_summary',''), 'selection': {'quote': s['selection'].get('quote','')} if s.get('selection') else None, 'ambiguous_matches':ambiguous, 'known_concepts': [{'id':c,'name':db.get(Concept,c).name} for c in [s['concept_id']]+[p['concept_id'] for p in s['plan']['prerequisite_support']]], 'available_source_ids': [r['id'] for r in source]}, ensure_ascii=False), handle_tool)
            s['trace'].extend(model_trace)
            all_sources = {r['id']: r for r in [*source, *tool_sources]}
            ids = set(all_sources)
            citations = citations_for(list(all_sources.values()))
            blocks = [b.model_dump() for b in output.blocks]
            if not blocks or not any(b['citation_ids'] for b in blocks) or blocks[0].get('kind')=='notice':raise ValueError('Incomplete teaching output or missing citations')
            known_ids={s['concept_id']}|{p['concept_id'] for p in s['plan']['prerequisite_support']}
            for block in blocks:
                if any(cid not in known_ids for cid in block.get('concept_ids',[])):raise ValueError('Unknown taught concept ID')
                if any(cid not in ids for cid in block['citation_ids']):
                    raise ValueError('Model returned citation outside retrieved scope')
            return {'blocks': blocks, 'citations': citations, 'pending_profile_updates': [], 'usage': usage, 'matching':{'decisions':[x.model_dump() for x in output.matches[:3]],'mapping':mapping,'metrics':matching_metrics}, 'thread_summary': (output.thread_summary or '')[:600], 'attribution': output.attribution.model_dump() if output.attribution else None, 'demo': False, 'trace': s['trace']}
        support = s['plan']['prerequisite_support']
        pre = [p for p in support if p['concept_id'] == 'linear_algebra.elementary_row_operations']
        depth = pre[0]['depth'] if pre else 'standard'
        example = '例如主元行 [2,1,4] 除以 2 后成为 [1,0.5,2]；另一行减去它，主元列归零。' if depth == 'detailed' else ''
        reminder = {'brief': '简短提醒：换基时用初等行变换把主元列化成单位列。', 'standard': '初等行变换作用于整行，换基时先缩放主元行，再消去其他行的主元列。', 'reminder':'提醒：主元归一化后消去同列其余元素。', 'none':'', 'detailed': '先复习初等行变换：交换行、非零倍缩放行、把一行的倍数加到另一行。' + example}.get(depth, '')
        blocks = [{'kind': 'main', 'markdown': f"## {db.get(Concept, s['concept_id']).name}\n\n{source[0]['text'][:700]}", 'citation_ids': [source[0]['id']]}]
        if reminder:
            blocks.insert(0, {'kind': 'prerequisite', 'markdown': reminder, 'citation_ids': [source[0]['id']]})
        if s['action'] == 'ask':
            blocks = [{'kind': 'answer', 'markdown': f"演示回答：{reminder or source[0]['text'][:300]}", 'citation_ids': [source[0]['id']]}]
        return {'blocks': blocks, 'citations': citations, 'pending_profile_updates': [], 'demo': True}
    graph = StateGraph(GraphState)
    graph.add_node('retrieve', retrieve)
    graph.add_node('compose', compose)
    graph.add_edge(START, 'retrieve')
    graph.add_edge('retrieve', 'compose')
    graph.add_edge('compose', END)
    return graph.compile().invoke(state)
