from uuid import uuid4
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from backend.app.db import SessionLocal
from backend.app import models as m
from backend.app import resolver,practice,policy
from backend.app.teaching import make_plan
from backend.app.mastery import settle
from tests.test_core import client

def test_id_alias_cache_embedding_no_dedicated_model(monkeypatch):
    calls=[]
    monkeypatch.setattr(resolver,'candidates',lambda *a:(calls.append('embedding') or [{'id':'linear_algebra.euclidean_dot_product','name':'点积','scope':'实欧氏空间','score':.99}]))
    with client,SessionLocal() as db:
        for term in ('点积','dot product','数量积'):
            r=resolver.resolve(db,name=term,context='real_euclidean');assert r['concept_id']=='linear_algebra.euclidean_dot_product' and r['extra_llm_calls']==0
        assert calls==[]
        r=resolver.resolve(db,name='一般复内积',definition='复向量空间的一般内积',context='complex')
        assert r['status']=='uncertain' and r['concept_id']!='linear_algebra.euclidean_dot_product'
        db.commit();again=resolver.resolve(db,name='一般复内积',definition='复向量空间的一般内积',context='complex')
        assert again['concept_id']==r['concept_id'] and len(calls)==1 and again['status']=='uncertain'
        for term in ('向量外积','偏导数','导数'):
            r=resolver.resolve(db,name=term,context='math');assert r['concept_id']!='linear_algebra.euclidean_dot_product' and r['extra_llm_calls']==0

def test_six_levels_and_background_do_not_overwrite():
    with client:
        body={'client_operation_id':'background-service','field':'人工智能','directions':['数学'],'starting':'高中水平'}
        first=client.post('/v1/background',json=body);assert first.status_code==200
        assert client.post('/v1/background',json=body).json()==first.json()
        states=client.get('/v1/knowledge').json()['states'];assert next(s for s in states if s['concept_id']=='math.functions')['mastery_level']==2
        with SessionLocal() as db:
            cid='linear_algebra.elementary_row_operations'
            assert next(s for s in states if s['concept_id']=='linear_algebra.inverse_matrix')['mastery_level']==0
            depths=[]
            for level in range(6):
                settle(db,str(uuid4()),cid,'manual',{'level':level})
                p=make_plan(db,'optimization_demo','optimization.simplex_tableau_pivot');depths.append(next(x for x in p['prerequisite_support'] if x['concept_id']==cid)['depth'])
            assert depths==['detailed','detailed','standard','brief','reminder','none']
            settle(db,str(uuid4()),'math.functions','manual',{'level':4})
        body['client_operation_id']='background-second';client.post('/v1/background',json=body)
        assert next(s for s in client.get('/v1/knowledge').json()['states'] if s['concept_id']=='math.functions')['mastery_level']==4

def test_practice_cache_reveal_grade_and_revoke(monkeypatch):
    calls=[]
    def parse(**kwargs):
        calls.append(kwargs)
        n=2+len(calls)
        output=practice.PracticeOutput(question=f'计算 2+{n}，仅填数字',answer=f'2+{n}={2+n}',hint='把两部分相加',rubric=f'答案{2+n}',difficulty=2,numeric_answer=2+n,verification_expression=f'2+{n}')
        return SimpleNamespace(output_parsed=output,usage=SimpleNamespace(model_dump=lambda:{'input_tokens':10,'output_tokens':10}))
    monkeypatch.setattr(practice,'configured',lambda:True)
    monkeypatch.setattr(practice,'client',lambda:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    with client,SessionLocal() as db:
        cid='linear_algebra.matrix';settle(db,str(uuid4()),cid,'manual',{'level':2})
        first=practice.generate(db,'linear_algebra_demo',cid,'practice-service-one')
        second=practice.generate(db,'linear_algebra_demo',cid,'practice-service-two')
        assert first['id']==second['id'] and len(calls)==1 and 'answer' not in first and 'hint' not in first
        practice.reveal(db,first['id'],'hint')
        r=practice.grade(db,first['id'],'answer-service-one','5')
        assert r['correct'] and r['assisted'] and r['mastery']['mastery_level']==2
        assert practice.grade(db,first['id'],'answer-service-retry','5')['duplicate'] and len(calls)==1
        fresh=practice.generate(db,'linear_algebra_demo',cid,'practice-service-fresh',True)
        r=practice.grade(db,fresh['id'],'answer-service-fresh','6');assert r['mastery']['mastery_level']==3
        assert not practice.verified_numeric({'numeric_answer':5,'verification_expression':"__import__('os').system('true')"})

def test_cached_lesson_read_does_not_call_model(monkeypatch):
    from backend.app import main
    with client:
        key='cache.'+str(uuid4())
        with SessionLocal() as db:
            db.add(m.Concept(id=key,name='cache test'));db.add(m.CourseConcept(course_id='linear_algebra_demo',concept_id=key,position=99));db.commit()
        calls=[]
        def fake(db,state):calls.append(state);return {'blocks':[{'kind':'main','markdown':'保存的讲义','citation_ids':[]}],'citations':[{'id':'test'}],'trace':[],'demo':True}
        monkeypatch.setattr(main,'run_graph',fake)
        path=f'/v1/courses/linear_algebra_demo/concepts/{key}/lesson'
        assert client.get(path).json()['lesson'] is None and not calls
        assert client.post(path).status_code==200
        assert client.post(path).json()['model_calls']==0 and len(calls)==1

def test_reorder_preserves_fixed_topics_and_rejects_loss(monkeypatch):
    from backend.app import reordering
    with client,SessionLocal() as db:
        course='order-'+str(uuid4());db.add(m.Course(id=course,title='排序测试'));db.flush()
        links=[]
        for i in range(4):
            cid=course+str(i);db.add(m.Concept(id=cid,name='主题'+str(i)));db.flush();link=m.CourseConcept(course_id=course,concept_id=cid,position=i,topic_id=cid);db.add(link);links.append(link)
        db.add(m.Progress(course_id=course,concept_id=links[2].concept_id));db.add(m.LearnerEvent(id=str(uuid4()),operation_id='order-done'+course,concept_id=links[0].concept_id,kind='concept_completed',before={},after={'course_id':course},created_at='test'));db.commit()
        def parse(**kw):return SimpleNamespace(output_parsed=reordering.ReorderOutput(topic_ids=[x.topic_id for x in reversed(links)]),usage=None)
        monkeypatch.setattr(reordering,'client',lambda:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
        assert reordering.reorder(db,course)['status']=='reordered'
        rows=db.scalars(select(m.CourseConcept).where(m.CourseConcept.course_id==course).order_by(m.CourseConcept.position)).all();assert [x.topic_id for x in rows]==[links[0].topic_id,links[3].topic_id,links[2].topic_id,links[1].topic_id]
        monkeypatch.setattr(reordering,'client',lambda:SimpleNamespace(responses=SimpleNamespace(parse=lambda **kw:SimpleNamespace(output_parsed=reordering.ReorderOutput(topic_ids=[]),usage=None))))
        assert reordering.reorder(db,course)['status']=='safe_append'

def test_merge_is_idempotent_preserves_topics_and_explicit_level():
    from backend.app.library import merge
    from backend.app.knowledge import snapshot
    from backend.app.mastery import settle
    with client,SessionLocal() as db:
        source='merge-source-'+str(uuid4());target='merge-target-'+str(uuid4())
        db.add_all([m.Concept(id=source,name='重复概念'),m.Concept(id=target,name='保留概念')]);db.flush()
        link=m.CourseConcept(course_id='linear_algebra_demo',concept_id=source,position=999);db.add(link);db.commit();topic=link.topic_id
        settle(db,str(uuid4()),source,'manual',{'level':2});settle(db,str(uuid4()),source,'complete',{'identity':'source-run'})
        settle(db,str(uuid4()),target,'manual',{'level':4})
        op=str(uuid4());first=merge(db,source,target,1,op)
        assert first['mastery_level']==1 and first['first_completed']
        assert merge(db,source,target,1,op)==first
        assert snapshot(db,source)['concept_id']==target
        assert db.get(m.CourseConcept,('linear_algebra_demo',source)).topic_id==topic
        assert snapshot(db,target)['review_progress']==0

def test_matching_piggyback_budget_and_conflict_guard():
    from backend.app import matching
    from backend.app.mastery import settle
    with client,SessionLocal() as db:
        target='match-target-'+str(uuid4());temporary='match-temp-'+str(uuid4())
        db.add(m.Concept(id=target,name='候选',scope='一个短范围'));db.add(m.Concept(id=temporary,name='暂存项',scope='同范围',details={'provisional':True,'candidates':[{'id':target,'name':'候选','scope':'一个短范围'}]}));db.commit()
        compact,mapping,metrics=matching.payload(db,[temporary]*5)
        assert metrics['input_tokens_estimated']<=600 and len(compact)<=3 and metrics['extra_llm_calls']==0
        # Learning evidence requires manual resolution, even if a model says same.
        settle(db,str(uuid4()),temporary,'manual',{'level':2})
        result=matching.apply(db,[{'query':'x0','candidate':'c0','relation':'same'}],mapping)
        assert result[0]['status']=='manual_conflict' and not (db.get(m.Concept,temporary).details or {}).get('redirect')
