"""Configuration must work in a fresh checkout without the developer's .env."""
import json,os,shutil,subprocess,sys
from pathlib import Path

def test_env_loaded_before_database_and_custom_directory_created(tmp_path):
    package=tmp_path/'backend'/'app';package.mkdir(parents=True)
    for name in ('__init__.py','config.py','db.py'):
        shutil.copyfile(Path('backend/app')/name,package/name)
    (package.parent/'__init__.py').write_text('')
    (package.parent/'.env').write_text('TUTOR_DB_URL=sqlite:///./nested/database/tutor.db\nTUTOR_DEMO_MODE=1\n')
    env={k:v for k,v in os.environ.items() if k not in {'TUTOR_DB_URL','OPENAI_API_KEY','PYTHONPATH'}}
    result=subprocess.run([sys.executable,'-c',"from backend.app.db import engine; import json; print(json.dumps({'database':engine.url.database})); engine.connect().close()"],cwd=tmp_path,env=env,capture_output=True,text=True,check=True)
    assert Path(json.loads(result.stdout)['database'])==tmp_path/'nested/database/tutor.db'
    assert (tmp_path/'nested/database/tutor.db').is_file()

def test_explicit_real_mode_without_key_fails_instead_of_demo(monkeypatch):
    from tests.test_core import client
    from backend.app import main
    from uuid import uuid4
    monkeypatch.setattr(main,'demo_requested',lambda:False)
    monkeypatch.setattr(main,'configured',lambda:False)
    with client:
        health=client.get('/health').json();assert health['demo_mode'] is False
        r=client.post('/v1/turns',json={'client_operation_id':str(uuid4()),'action':'teach','course_id':'linear_algebra_demo','concept_id':'linear_algebra.inverse_matrix'})
        assert r.status_code==503 and 'API Key' in r.json()['detail']

def test_authored_pdf_parses_without_external_materials():
    import pymupdf
    from backend.app.ingestion import parse_upload
    with pymupdf.open() as document:
        page=document.new_page()
        page.insert_text((72,72),'Project-authored demo: x^2 + y^2 = 1')
        content=document.tobytes()
    pages=parse_upload('authored-demo.pdf',content)
    assert len(pages)==1 and 'x^2 + y^2 = 1' in pages[0]
