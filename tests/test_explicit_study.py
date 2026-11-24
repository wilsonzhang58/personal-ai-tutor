from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from tests.test_core import client
from backend.app.db import SessionLocal
from backend.app import models as m,main,generation,dependencies,policy
from backend.app.mastery import settle

def topic():
    cid='explicit.'+str(uuid4())
    with SessionLocal() as db:
        db.add(m.Concept(id=cid,name='显式开始测试'));db.add(m.CourseConcept(course_id='linear_algebra_demo',concept_id=cid,position=999));db.commit()
    return cid,f'/v1/courses/linear_algebra_demo/concepts/{cid}'

def test_explicit_task_is_read_only_until_start_and_idempotent(monkeypatch):
    with client:
        cid,path=topic();calls=[];launched=[]
        monkeypatch.setattr(generation,'launch',lambda job:launched.append(job))
        monkeypatch.setattr(dependencies,'analyze',lambda *a:(calls.append('analysis') or {'model_calls':0,'usage':{}}))
        def graph(db,state):
            calls.append('generate');assert state['plan']['target_concept_id']==cid
            return {'blocks':[{'kind':'main','markdown':'带引用讲义','citation_ids':['test']}],'citations':[{'id':'test'}],'trace':[],'demo':True}
        monkeypatch.setattr(main,'run_graph',graph)
        assert client.get(path+'/generation').json()['status']=='idle'
        assert client.get(path+'/lesson').json()['lesson'] is None
        assert not calls and not launched
        with ThreadPoolExecutor(max_workers=5) as pool:
            jobs=list(pool.map(lambda _:client.post(path+'/generation').json(),range(5)))
        assert len(launched)==1 and len({j['job_id'] for j in jobs})==1 and not calls
        job=jobs[0]['job_id'];assert client.get(path+'/generation').json()['status']=='queued'
        generation.run(job)
        assert calls==['analysis','generate']
        ready=client.get(path+'/generation').json();assert ready['status']=='completed' and ready['lesson']
        for _ in range(3):
            assert client.post(path+'/generation').json()['lesson']['id']==ready['lesson']['id']
            assert client.get(path+'/generation').json()['model_calls']==0
        assert calls==['analysis','generate'] and len(launched)==1
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(m.StudyRun).where(m.StudyRun.concept_id==cid))==0
            assert not db.get(m.LearnerState,('local_user',cid)).mastery_data.get('first_completed')

def test_failure_retry_reuses_task_and_never_completes(monkeypatch):
    with client:
        cid,path=topic();monkeypatch.setattr(generation,'launch',lambda *_:None)
        monkeypatch.setattr(dependencies,'analyze',lambda *a:{'model_calls':0,'usage':{}})
        def broken(*a):raise ValueError('provider unavailable')
        monkeypatch.setattr(main,'run_graph',broken)
        job=client.post(path+'/generation').json()['job_id'];generation.run(job)
        failed=client.get(path+'/generation').json();assert failed['status']=='failed' and failed['error'] and failed['lesson'] is None
        with SessionLocal() as db:
            assert not db.scalar(select(m.Lesson).where(m.Lesson.concept_id==cid))
            assert not db.get(m.LearnerState,('local_user',cid))
        retry=client.post(path+'/generation').json();assert retry['job_id']==job
        monkeypatch.setattr(main,'run_graph',lambda *a:{'blocks':[{'kind':'main','markdown':'成功','citation_ids':['c']}],'citations':[{'id':'c'}],'trace':[],'demo':True})
        generation.run(job);assert client.get(path+'/generation').json()['status']=='completed'

def test_library_filters_before_pagination_and_local_search():
    with client,SessionLocal() as db:
        prefix=str(uuid4())
        for i in range(35):
            cid=f'filter.{prefix}.{i}'
            db.add(m.Concept(id=cid,name=f'测试{prefix}-{i:02}',details={'subject':'数学','direction':'线性代数','english_name':f'English {prefix}-{i:02}'}));db.flush()
            db.add(m.LearnerState(profile_id='local_user',concept_id=cid,mastery_level=2,state_version=1));db.add(m.ConceptAlias(alias='late-alias-'+prefix,context='',concept_id=cid)) if i==34 else None
        db.add(m.Concept(id='ai.'+prefix,name='测试'+prefix,details={'subject':'人工智能','direction':'基础'}));db.commit()
        base='/v1/library';params={'q':prefix,'subject':'数学','direction':'线性代数','mastery':2,'page_size':10}
        first=client.get(base,params=params).json();assert first['total']==35 and len(first['items'])==10
        last=client.get(base,params={**params,'page':4}).json();assert len(last['items'])==5
        alias=client.get(base,params={**params,'q':'late-alias-'+prefix}).json();assert alias['total']==1 and alias['items'][0]['concept_id'].endswith('.34')
        assert client.get(base,params={**params,'q':'English '+prefix+'-34'}).json()['total']==1
        assert client.get(base,params={**params,'mastery':3}).json()['total']==0
        assert client.get(base,params={**params,'subject':'人工智能'}).json()['total']==0
        assert client.get(base,params={**params,'direction':'微积分'}).json()['total']==0
        tax=client.get('/v1/library/taxonomy').json()
        assert all('基础' in v and '其他' in v for v in tax.values())
        row=alias['items'][0];assert 'last_reason' not in row and 'evidence_source' not in row and 'review_progress' not in row
        changed=client.post('/v1/knowledge/events',json={'client_operation_id':str(uuid4()),'concept_id':row['concept_id'],'mastery_level':3,'expected_version':1})
        assert changed.status_code==200 and changed.json()['mastery_level']==3

def test_completed_run_revisit_today_stays_deduplicated(monkeypatch):
    from datetime import datetime,timezone,timedelta
    with client,SessionLocal() as db:
        cid,path=topic();lesson_id=str(uuid4())
        db.add(m.Lesson(id=lesson_id,course_id='linear_algebra_demo',concept_id=cid,version=1,learner_version=0,blocks=[{'kind':'main'}],citations=[{'id':'c'}],teaching_plan={}));db.commit()
        moment=datetime(2026,11,24,4,tzinfo=timezone.utc);monkeypatch.setattr(policy,'now',lambda:moment)
        def complete():
            r=client.post('/v1/runs',json={'client_operation_id':str(uuid4()),'lesson_id':lesson_id}).json()
            return r,client.post('/v1/runs/'+r['run_id']+'/complete',json={'client_operation_id':'complete:'+r['run_id']}).json()
        one,a=complete();two,b=complete();assert one['run_id']==two['run_id'] and a['mastery_level']==b['mastery_level']==1
        moment+=timedelta(days=1)
        three,c=complete();assert three['run_id']!=one['run_id'] and c['review_progress']==1 and c['mastery_level']==1
        four,d=complete();assert four['run_id']==three['run_id'] and d['review_progress']==1

def test_correct_page_reuses_other_vectors_and_anchors_without_generation(monkeypatch):
    from backend.app.ingestion import confirm
    from backend.app import retrieval
    import torch
    with client,SessionLocal() as db:
        cid,path=topic();material=m.Material(id=str(uuid4()),course_id='linear_algebra_demo',title='test.pdf',sha256='test',pages=[]);db.add(material);db.commit()
        confirm(db,material,['old page','unchanged page']);old=db.scalars(select(m.SourceChunk).where(m.SourceChunk.material_id==material.id)).all()
        for r in old:r.embedding=[1.,0.];r.embedding_model=retrieval.MODEL_NAME
        db.get(m.CourseConcept,('linear_algebra_demo',cid)).source_chunk_id=old[0].id;db.commit()
        calls=[]
        monkeypatch.setattr(retrieval,'encode',lambda texts,prefix:(calls.append((texts,prefix)) or torch.tensor([[1.,0.] for _ in texts])))
        def forbidden(*a,**kw):raise AssertionError('No generation during correction')
        monkeypatch.setattr(main,'run_graph',forbidden)
        response=client.patch('/v1/materials/'+material.id,json={'pages':['fixed page','unchanged page'],'expected_version':1});assert response.status_code==200
        assert not calls
        db.expire_all();live=db.scalars(select(m.SourceChunk).where(m.SourceChunk.material_id==material.id,m.SourceChunk.material_version==2)).all()
        assert live[1].embedding==[1.,0.] and live[0].embedding is None
        assert db.get(m.CourseConcept,('linear_algebra_demo',cid)).source_chunk_id==live[0].id
        retrieval.search(db,'query','linear_algebra_demo')
        passages=[t for texts,prefix in calls if prefix=='passage: ' for t in texts]
        assert 'fixed page' in passages and 'unchanged page' not in passages
        assert client.patch('/v1/materials/'+material.id,json={'pages':['stale','unchanged page'],'expected_version':1}).status_code==409
        assert client.get(path+'/lesson').json()['lesson'] is None

def test_model_context_keeps_teaching_but_omits_ledger_and_explanations():
    from backend.app.graph import model_plan
    from backend.app.llm import ModelOutput
    plan={'target_concept_id':'target','target_state':{'mastery_level':2,'last_reason':'private ledger','review_progress':2},'profile_version':100,'response_language':'zh-CN','prerequisite_support':[{'concept_id':'pre','mastery_level':1,'depth':'detailed','guidance':'必要的基础和例题','necessity':{'location':'公式使用处'},'reason':'ledger reason','state_version':12}]}
    compact=model_plan(plan)
    assert compact['target_mastery_level']==2 and compact['prerequisite_support'][0]['guidance']=='必要的基础和例题'
    assert 'profile_version' not in compact and 'target_state' not in compact
    assert 'reason' not in compact['prerequisite_support'][0] and 'state_version' not in compact['prerequisite_support'][0]
    schema=ModelOutput.model_json_schema()
    assert 'reason' not in schema['$defs']['QuestionAttribution']['properties']
    assert 'pending_profile_updates' not in schema['properties']

def test_local_classification_preserves_clear_direction_and_handles_shared_basics():
    from backend.app.classification import classify
    assert classify(m.Concept(id='statistics.descriptive',name='描述统计',details={'category':'数学/概率统计'}))==('数学','统计')
    assert classify(m.Concept(id='probability.basic',name='基础概率',details={'category':'数学/概率统计'}))==('数学','概率论')
    assert classify(m.Concept(id='math.functions',name='函数',details={'category':'数学/函数'}))==('数学','基础')
    assert classify(m.Concept(id='linear_algebra.matrix',name='矩阵',details={}))==('数学','线性代数')
    assert classify(m.Concept(id='custom.topic',name='Uncertain',details={'category':'数学/其他'}))==('数学','其他')
    assert classify(m.Concept(id='custom.future',name='future',details={'subject':'物理','direction':'力学'}))==('物理','力学')
