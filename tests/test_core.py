import os
import tempfile
from pathlib import Path
os.environ['TUTOR_DB_URL'] = 'sqlite:///' + str(Path(tempfile.mkdtemp()) / 'test.db')
os.environ['TUTOR_DEMO_MODE'] = '1'
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.db import SessionLocal
from backend.app.models import LearnerEvent, Progress, Note
from backend.app.teaching import make_plan
from backend.app.retrieval import top_k
from sqlalchemy import select

client = TestClient(app)

def test_cross_course_state_and_idempotency():
    with client:
        key = 'linear_algebra.elementary_row_operations'
        event = {'client_operation_id':'review-1','concept_id':key,'familiarity':'needs_review','expected_version':0}
        assert client.post('/v1/knowledge/events',json=event).status_code == 200
        assert client.post('/v1/knowledge/events',json=event).status_code == 200
        with SessionLocal() as db:
            p = make_plan(db,'optimization_demo','optimization.simplex_tableau_pivot')
            assert next(x for x in p['prerequisite_support'] if x['concept_id']==key)['depth']=='detailed'
            assert db.scalar(select(LearnerEvent).where(LearnerEvent.operation_id=='review-1')) is not None
        state = next(x for x in client.get('/v1/knowledge').json()['states'] if x['concept_id']==key)
        assert state['state_version']==1
        assert client.post('/v1/knowledge/events',json={'client_operation_id':'stale','concept_id':key,'familiarity':'unknown','expected_version':0}).status_code==409
        assert client.post('/v1/knowledge/events',json={'client_operation_id':'familiar','concept_id':key,'familiarity':'self_reported_familiar','support_override':'brief','expected_version':1}).status_code==200
        with SessionLocal() as db:
            p=make_plan(db,'optimization_demo','optimization.simplex_tableau_pivot')
            assert next(x for x in p['prerequisite_support'] if x['concept_id']==key)['depth']=='brief'
        assert next(x for x in client.get('/v1/knowledge').json()['states'] if x['concept_id']=='linear_algebra.inverse_matrix')['familiarity']=='unknown'

def test_tensor_topk():
    import torch
    v=torch.tensor([[1.,0.],[0.,1.],[.8,.2]])
    assert [i for i,_ in top_k(torch.tensor([1.,0.]),v,4)] == [0,2,1]
    assert top_k(torch.tensor([1.,0.]),torch.empty((0,2)),4)==[]

def test_notes_and_progress():
    with client:
        # Retrieval is injected here; the real E5 smoke test is separate.
        from backend.app import graph
        from backend.app.models import SourceChunk
        graph.SourceChunk=SourceChunk
        original=graph.search_course_materials
        graph.search_course_materials=lambda db,q,c,allowed:[{'id':db.query(graph.SourceChunk).filter_by(course_id=c).first().id,'course_id':c,'material_id':next(iter([x.id for x in db.query(graph.Material).filter_by(course_id=c)])),'page':1,'text':'Demo source','score':1.0}]
        try:
            r=client.post('/v1/turns',json={'client_operation_id':'lesson-note','action':'teach','course_id':'optimization_demo','concept_id':'optimization.simplex_tableau_pivot'}).json()
        finally: graph.search_course_materials=original
        assert r.get('status')=='completed',r
        lesson_id=r['lesson_id']; block_id=r['blocks'][0]['block_id']
        note={'client_operation_id':'note-1','lesson_id':lesson_id,'block_id':block_id,'anchor':{'quote':'Demo','start':0,'end':4},'text':'My note'}
        assert client.post('/v1/notes',json=note).status_code==200
        assert client.post('/v1/notes',json=note).status_code==200
        assert len(client.get(f'/v1/lessons/{lesson_id}/notes').json())==1
        graph.search_course_materials=lambda db,q,c,allowed:[{'id':db.query(graph.SourceChunk).filter_by(course_id=c).first().id,'course_id':c,'material_id':db.query(graph.Material).filter_by(course_id=c).first().id,'page':1,'text':'Demo source','score':1.0}]
        try:
            ask=client.post('/v1/turns',json={'client_operation_id':'ask-note','action':'ask','course_id':'optimization_demo','concept_id':'optimization.simplex_tableau_pivot','lesson_id':lesson_id,'thread_id':r['thread_id'],'selection':{'block_id':block_id,'quote':r['blocks'][0]['markdown'][:4],'start':0,'end':4},'message':'这里为什么这样做？'})
        finally: graph.search_course_materials=original
        assert ask.status_code==200,ask.text
        with SessionLocal() as db:
            event=db.scalar(select(LearnerEvent).where(LearnerEvent.operation_id=='question:ask-note'))
            assert event.kind=='question_observed'
            assert event.before['familiarity']==event.after['familiarity']
        progress={'client_operation_id':'progress-1','concept_id':'optimization.simplex_tableau_pivot','lesson_id':lesson_id,'block_id':block_id,'offset':120}
        assert client.put('/v1/courses/optimization_demo/progress',json=progress).status_code==200
        resumed=client.get('/v1/courses/optimization_demo/resume').json()
        assert resumed['progress']['offset']==120 and resumed['notes'][0]['text']=='My note'

def test_interrupted_turn_retries_same_operation():
    from backend.app import main
    from backend.app.models import Turn
    with client:
        with SessionLocal() as db:
            row=Turn(id='interrupted-turn',operation_id='interrupted-op',action='teach',course_id='optimization_demo',concept_id='optimization.optimality_check',request={},status='pending')
            db.add(row);db.commit()
        main.startup()
        assert client.get('/v1/turns/interrupted-turn').json()['status']=='interrupted'
        original=main.run_graph
        main.run_graph=lambda db,state:{'blocks':[{'kind':'notice','markdown':'No source','citation_ids':[]}],'citations':[],'trace':[],'demo':True,'pending_profile_updates':[]}
        try:
            r=client.post('/v1/turns',json={'client_operation_id':'interrupted-op','action':'teach','course_id':'optimization_demo','concept_id':'optimization.optimality_check'})
        finally:main.run_graph=original
        assert r.status_code==200 and r.json()['turn_id']=='interrupted-turn'
        assert r.json()['status']=='insufficient_evidence'
        with SessionLocal() as db:
            assert len(db.scalars(select(Turn).where(Turn.operation_id=='interrupted-op')).all())==1

def test_second_question_receives_thread_history(monkeypatch):
    from backend.app import main
    captured=[]
    def fake_graph(db,state):
        captured.append(state)
        return {'blocks':[{'kind':'main','markdown':'原文答案','citation_ids':[]}],'citations':[{'id':'test'}] if state['action']=='teach' else [],'trace':[],'demo':True,'pending_profile_updates':[]}
    monkeypatch.setattr(main,'run_graph',fake_graph)
    with client:
        teach=client.post('/v1/turns',json={'client_operation_id':'history-teach','action':'teach','course_id':'optimization_demo','concept_id':'optimization.slack_variable'}).json()
        block=teach['blocks'][0]
        payload={'action':'ask','course_id':'optimization_demo','concept_id':'optimization.slack_variable','lesson_id':teach['lesson_id'],'thread_id':teach['thread_id'],'selection':{'block_id':block['block_id'],'start':0,'end':2,'quote':'原文'}}
        first=client.post('/v1/turns',json={**payload,'client_operation_id':'history-ask-1','message':'第一问'}).json()
        second=client.post('/v1/turns',json={**payload,'client_operation_id':'history-ask-2','message':'继续追问'}).json()
        assert first['status']=='insufficient_evidence' and second['status']=='insufficient_evidence'
        assert any(item['question']=='第一问' for item in captured[-1]['history'])

def test_upload_preview_and_confirm_plan(monkeypatch):
    from backend.app import planning
    class Parsed:
        concepts=[planning.Candidate(id='test.imported',name='导入知识点',source_chunk_id='placeholder')]
        prerequisites=[]
    with client:
        c=client.post('/v1/courses',json={'title':'导入课程'}).json()
        preview=client.post(f"/v1/courses/{c['id']}/materials",files={'file':('lesson.md',b'# Topic\n\nOriginal formula $x^2$.','text/markdown')}).json()
        assert preview['pages'][0].startswith('# Topic')
        assert client.patch('/v1/materials/'+preview['id'],json={'pages':['# Topic\n\nCorrected formula $x^2$.']}).status_code==200
        from backend.app.models import SourceChunk
        with SessionLocal() as db: chunk=db.scalar(select(SourceChunk).where(SourceChunk.material_id==preview['id']))
        plan={'concepts':[{'id':'test.imported','name':'导入知识点','source_chunk_id':chunk.id}],'prerequisites':[]}
        assert client.put(f"/v1/courses/{c['id']}/plan",json=plan).status_code==200
        assert any(course['concepts'][0]['id']=='test.imported' for course in client.get('/v1/courses').json() if course['id']==c['id'])
        bad={**plan,'concepts':[{**plan['concepts'][0],'source_chunk_id':'foreign'}]}
        assert client.put(f"/v1/courses/{c['id']}/plan",json=bad).status_code==422
        from backend.app import graph
        monkeypatch.setattr(graph,'search_course_materials',lambda *args,**kwargs:[])
        # A confirmed plan source must survive even an empty semantic ranking.
        generated=client.post('/v1/turns',json={'client_operation_id':'imported-anchor-teach','action':'teach','course_id':c['id'],'concept_id':'test.imported'}).json()
        assert chunk.id in {citation['id'] for citation in generated['citations']}

def test_folder_batch_partial_retry_and_collisions(monkeypatch,tmp_path):
    from backend.app import storage
    from backend.app.models import CourseFolder,MaterialFile,Material
    monkeypatch.setattr(storage,'ROOT',tmp_path)
    monkeypatch.setattr(storage,'PRIVATE_ROOT',tmp_path/'private_materials')
    with client:
        # New folder/course is idempotent, and private root traversal is rejected.
        mapping=client.post('/v1/folders',json={'name':'New course'}).json()
        assert client.post('/v1/folders',json={'name':'New course'}).json()==mapping
        assert client.post('/v1/folders',json={'name':'../outside'}).status_code==422
        path='/v1/folders/New%20course/materials'
        response=client.post(path,files=[('files',('lecture.md',b'# First','text/markdown')),('files',('bad.pdf',b'not pdf','application/pdf'))]).json()
        good,bad=response['files'];assert good['status']=='parsed' and bad['status']=='failed'
        assert (tmp_path/good['saved_path']).read_bytes()==b'# First'
        duplicate=client.post(path,files={'files':('other.md',b'# First','text/markdown')}).json()['files'][0]
        assert duplicate['id']==good['id'] and duplicate['status']=='duplicate'
        collision=client.post(path,files={'files':('lecture.md',b'# Second','text/markdown')}).json()['files'][0]
        assert collision['saved_path']!=good['saved_path']
        assert (tmp_path/good['saved_path']).read_bytes()==b'# First'
        retry=client.post(path,files={'files':('bad.md',b'# Fixed','text/markdown')}).json()['files'][0]
        assert retry['status']=='parsed'
        main=__import__('backend.app.main',fromlist=['startup']);main.startup()
        assert any(f['name']=='New course' and f['course_id']==mapping['course_id'] for f in client.get('/v1/folders').json())
        with SessionLocal() as db:
            assert len(db.scalars(select(Material).where(Material.course_id==mapping['course_id'])).all())==3
            assert db.get(MaterialFile,good['id']) is not None
