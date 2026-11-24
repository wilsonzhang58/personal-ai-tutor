"""Real E5 smoke check over the confirmed demo chunks."""
from backend.app.db import SessionLocal
from backend.app.retrieval import search
with SessionLocal() as db:
    rows=search(db,'单纯形表的主元行怎样做初等行变换？','optimization_demo',allowed_course_ids=['optimization_demo','linear_algebra_demo'])
    print([{'id':x['id'],'score':round(x['score'],3),'page':x['page']} for x in rows])
