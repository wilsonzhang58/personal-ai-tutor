from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from backend.app import policy
from backend.app.db import SessionLocal
from backend.app import models as m
from backend.app.mastery import settle,revoke,view
from tests.test_core import client

class Learner:
    def __init__(self,level=0):self.level=level;self.data=policy.empty();self.trace=[]
    def event(self,kind,day,**payload):
        self.level,self.data,reason=policy.reduce(self.level,self.data,kind,payload,day);self.trace.append((self.level,self.data['review_count'],reason));return self

@pytest.mark.parametrize('initial',[0,2,4])
def test_first_then_three_different_review_days(initial):
    s=Learner(initial).event('complete','2026-11-01',identity='first')
    assert s.level==initial+1
    s.event('complete','2026-11-01',identity='same-day');assert s.data['review_count']==0
    if initial==4:
        s.event('complete','2026-11-02',identity='ceiling');assert s.level==5 and s.data['review_count']==0;return
    for index in (2,3):
        s.event('complete',f'2026-11-0{index}',identity=f'day{index}');assert s.data['review_count']==index-1
    s.event('complete','2026-11-04',identity='third');assert s.level==initial+2 and s.data['review_count']==0
    s.event('complete','2026-11-05',identity='fourth');assert s.data['review_count']==1
    assert s.trace[:4][0][0]==initial+1

def test_up_limit_first_fact_after_practice_and_counter_cleaning():
    s=Learner(2);s.data.update(doubt_count=2,error_count=1,review_count=2)
    s.event('practice','2026-11-01',identity='q1',correct=True,reliable=True,assisted=False,difficulty=2)
    assert s.level==3 and [s.data[k] for k in ('review_count','doubt_count','error_count')]==[0,0,0]
    s.event('complete','2026-11-01',identity='first');assert s.level==3 and s.data['first_completed']
    s.event('complete','2026-11-02',identity='review');assert s.data['review_count']==1
    s.event('practice','2026-11-02',identity='easy',correct=True,reliable=True,assisted=False,difficulty=0);assert s.level==3
    s.event('practice','2026-11-02',identity='assisted',correct=True,reliable=True,assisted=True,difficulty=4);assert s.level==3

@pytest.mark.parametrize('initial',[0,1,3])
def test_three_doubts_and_two_errors_floor_and_shared_daily_limit(initial):
    s=Learner(initial)
    for n in range(3):s.event('question','2026-11-01',identity=f'episode{n}',intent='basic_difficulty',clear_basic=True,resolved=True,is_target=False)
    assert s.level==max(1,initial-1) if initial>0 else s.level==0
    assert s.data['doubt_count']==0
    if initial==3:
        for n in range(4):s.event('practice','2026-11-01',identity=f'wrong{n}',correct=False,reliable=True,basic_error=True,assisted=False,difficulty=1)
        assert s.level==2 and s.data['error_count']==0
    s2=Learner(3)
    for n in range(2):s2.event('practice','2026-11-01',identity=f'wrong{n}',correct=False,reliable=True,basic_error=True,assisted=False,difficulty=2)
    assert s2.level==2 and s2.data['error_count']==0

@pytest.mark.parametrize('intent,target,certain', [('exploration',False,True),('basic_difficulty',True,True),('basic_difficulty',False,False)])
def test_question_no_penalty_for_target_exploration_uncertainty(intent,target,certain):
    s=Learner(3)
    for n in range(5):s.event('question','2026-11-01',identity=str(n),intent=intent,clear_basic=True,resolved=certain,is_target=target)
    assert s.level==3 and s.data['doubt_count']==0

def test_repeat_episode_and_practice_never_count_twice():
    s=Learner(3)
    for n in range(5):s.event('question','2026-11-01',identity='thread',intent='basic_difficulty',clear_basic=True,resolved=True,is_target=False)
    assert s.data['doubt_count']==1
    for n in range(5):s.event('practice','2026-11-01',identity='same_question',correct=False,reliable=True,assisted=False,difficulty=3,basic_error=True)
    assert s.data['error_count']==1
    s.event('practice','2026-11-02',identity='challenge',correct=False,reliable=True,assisted=False,difficulty=5,basic_error=True)
    assert s.level==3 and s.data['error_count']==1

def test_positive_confirmation_and_timezone():
    s=Learner(5);s.data.update(doubt_count=2,error_count=1,review_count=0)
    s.event('practice','2026-11-01',identity='valid',correct=True,reliable=True,assisted=False,difficulty=5)
    assert s.level==5 and s.data['doubt_count']==0 and s.data['error_count']==0
    assert policy.local_date(datetime(2026,11,1,16,0,tzinfo=timezone.utc))=='2026-11-02'

def new_concept():
    cid='test.'+str(uuid4())
    with SessionLocal() as db:db.add(m.Concept(id=cid,name=cid));db.commit()
    return cid

def at(day):return datetime(2026,11,day,12,tzinfo=ZoneInfo('Asia/Hong_Kong'))

def test_transactional_concurrency_and_revoke_preserves_later_self_report():
    with client:
        cid=new_concept()
        with SessionLocal() as db:
            settle(db,'base-'+cid,cid,'manual',{'level':2},moment=at(1))
            state=db.get(m.LearnerState,('local_user',cid));state.mastery_data={**state.mastery_data,'first_completed':True,'review_count':2};db.commit()
        def submit(kind):
            with SessionLocal() as db:return settle(db,kind+cid,cid,kind,{'identity':kind,'correct':True,'reliable':True,'assisted':False,'difficulty':2},moment=at(2))
        with ThreadPoolExecutor(2) as pool:list(pool.map(submit,['complete','practice']))
        with SessionLocal() as db:
            assert view(db.get(m.LearnerState,('local_user',cid)))['mastery_level']==3
            assert db.get(m.LearnerState,('local_user',cid)).mastery_data['review_count']==0
        cid2=new_concept()
        with SessionLocal() as db:
            settle(db,'manual-'+cid2,cid2,'manual',{'level':3},moment=at(1))
            first=None
            for i in range(3):
                r=settle(db,f'question{i}'+cid2,cid2,'question',{'identity':str(i),'clear_basic':True,'intent':'basic_difficulty','resolved':True,'is_target':False},moment=at(2));first=first or r['event_id']
            assert r['mastery_level']==2
            r=revoke(db,'revoke-'+cid2,first);assert r['mastery_level']==3
            settle(db,'later-'+cid2,cid2,'manual',{'level':4},moment=at(3))
            events=db.scalars(select(m.LearnerEvent).where(m.LearnerEvent.concept_id==cid2,m.LearnerEvent.kind=='mastery_question')).all()
            r=revoke(db,'revoke-second-'+cid2,events[1].id);assert r['mastery_level']==4

def test_cross_course_first_completion_and_run_retries(monkeypatch):
    from backend.app import mastery
    monkeypatch.setattr(policy,'now',lambda:at(10))
    with client:
        cid=new_concept()
        with SessionLocal() as db:
            for n,course in enumerate(['linear_algebra_demo','optimization_demo']):
                db.add(m.CourseConcept(course_id=course,concept_id=cid,position=100))
                db.add(m.Lesson(id='cross-lesson-'+str(n)+cid,course_id=course,concept_id=cid,version=1,learner_version=0,blocks=[{'kind':'main','markdown':'test'}],citations=[{'id':'test'}],teaching_plan={}))
            db.commit()
        runs=[]
        for n in range(2):
            r=client.post('/v1/runs',json={'client_operation_id':f'cross-run-{n}'+cid,'lesson_id':'cross-lesson-'+str(n)+cid}).json();runs.append(r['run_id'])
        def finish(run):return client.post(f'/v1/runs/{run}/complete',json={'client_operation_id':'complete-'+run}).json()
        first=finish(runs[0]);second=finish(runs[1]);assert first['mastery_level']==second['mastery_level']==1 and second['review_progress']==0
        assert finish(runs[0])==first
        with SessionLocal() as db:
            assert all(db.get(m.StudyRun,r).completed for r in runs)

def test_positive_confirmation_preserves_review_credit_and_old_grade_cannot_override_manual():
    s=Learner(3);s.data.update(first_completed=True,review_count=2,doubt_count=2,error_count=1,last_up='2026-11-02')
    s.event('practice','2026-11-02',identity='correct',correct=True,reliable=True,assisted=False,difficulty=3)
    assert s.level==3 and s.data['review_count']==2 and s.data['doubt_count']==s.data['error_count']==0
    with client:
        cid=new_concept()
        with SessionLocal() as db:
            old=settle(db,str(uuid4()),cid,'manual',{'level':2},moment=at(1))
            settle(db,str(uuid4()),cid,'manual',{'level':4},moment=at(2))
            r=settle(db,str(uuid4()),cid,'practice',{'identity':'stale-result','correct':True,'reliable':True,'assisted':False,'difficulty':5,'state_version_used':old['state_version']},moment=at(2))
            assert r['mastery_level']==4 and r['evidence_source']=='manual_self_report'
