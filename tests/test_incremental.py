from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select
from backend.app import incremental
from backend.app.planning import CoursePlan,Candidate
from backend.app.models import Material,CourseConcept
from backend.app.db import SessionLocal
from tests.test_core import client

def test_incremental_append_dedup_and_no_repeat_model(monkeypatch):
    from backend.app.resolver import resolve
    monkeypatch.setattr(incremental,'resolve_batch',lambda db,items,context:[resolve(db,name=x['name'],context=context,allow_embedding=False) for x in items])
    calls=[]
    def parse(**kwargs):
        import json
        context=json.loads(kwargs['input']);calls.append(context)
        index=len(calls)
        return SimpleNamespace(output_parsed=CoursePlan(concepts=[Candidate(id=f'new_{index}',name=f'新概念{index}',source_chunk_id=context['new_sources'][0][0])],prerequisites=[]),usage=SimpleNamespace(model_dump=lambda:{'input_tokens':100,'output_tokens':40}))
    monkeypatch.setattr(incremental,'configured',lambda:True)
    monkeypatch.setattr(incremental,'client',lambda:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    with client:
        c=client.post('/v1/courses',json={'title':'incremental'}).json()['id']
        with SessionLocal() as db:
            db.add(Material(id='inc-1',course_id=c,title='a.md',sha256='a',pages=['# Topic A\nAlpha explanation']))
            db.commit()
        first=client.post(f'/v1/courses/{c}/sync').json();assert first['added']==1
        assert client.post(f'/v1/courses/{c}/sync').json()['added']==0 and len(calls)==1
        with SessionLocal() as db:
            db.add(Material(id='inc-2',course_id=c,title='b.md',sha256='b',pages=['# Topic A\nAlpha explanation','# Topic B\nNew beta']))
            db.commit()
        second=client.post(f'/v1/courses/{c}/sync').json();assert second['added']==1 and second['skipped_chunks']==1
        assert 'Alpha explanation' not in str(calls[-1]['new_sources'])
        with SessionLocal() as db:
            links=db.scalars(select(CourseConcept).where(CourseConcept.course_id==c).order_by(CourseConcept.position)).all()
            assert len(links)==2 and links[0].position==0 and links[1].position==1
            first_id=links[0].concept_id
        assert client.post(f'/v1/courses/{c}/sync').json()['added']==0 and len(calls)==2
        p=client.put(f'/v1/courses/{c}/progress',json={'client_operation_id':str(uuid4()),'concept_id':first_id,'offset':827.5,'completed':True})
        assert p.status_code==200
        assert client.get(f'/v1/courses/{c}/resume').json()['progress']['offset']==828
        course=next(x for x in client.get('/v1/courses').json() if x['id']==c)
        assert not course['concepts'][0]['completed']  # Navigation cannot complete learning.

def test_rendered_selection_anchor(monkeypatch):
    import hashlib
    from backend.app import main
    def fake(db,state):return {'blocks':[{'kind':'main','markdown':'## 标题\n\n**加粗**内容','citation_ids':[]}],'citations':[{'id':'test'}],'trace':[],'demo':True}
    monkeypatch.setattr(main,'run_graph',fake)
    with client:
        r=client.post('/v1/turns',json={'client_operation_id':'rendered-lesson','action':'teach','course_id':'optimization_demo','concept_id':'optimization.linear_programming'}).json()
        block=r['blocks'][0];payload={'client_operation_id':'rendered-ask','action':'ask','course_id':'optimization_demo','concept_id':'optimization.linear_programming','lesson_id':r['lesson_id'],'message':'为什么？','selection':{'format':'rendered','block_id':block['block_id'],'block_ids':[block['block_id']],'block_hashes':{block['block_id']:hashlib.sha256(block['markdown'].encode()).hexdigest()},'quote':'加粗内容'}}
        assert client.post('/v1/turns',json=payload).status_code==200
        payload['client_operation_id']='invalid-rendered';payload['selection']['block_hashes']={}
        assert client.post('/v1/turns',json=payload).status_code==422
