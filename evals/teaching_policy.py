from backend.app.db import SessionLocal
from backend.app.teaching import make_plan
with SessionLocal() as db:
    print(make_plan(db,'optimization_demo','optimization.simplex_tableau_pivot'))
