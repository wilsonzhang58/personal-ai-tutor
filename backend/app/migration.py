"""Additive, repeatable SQLite upgrade; no lesson or learner rows are replaced."""
from sqlalchemy import inspect, text, select
from uuid import uuid4
from .db import engine,Base,SessionLocal
from . import models as m

def migrate():
    additions={'concept_alias':{'language':"VARCHAR NOT NULL DEFAULT 'auto'"},'profile':{'settings':"JSON NOT NULL DEFAULT '{}'"},'course':{'route_version':'INTEGER NOT NULL DEFAULT 1'},'concept':{'details':"JSON NOT NULL DEFAULT '{}'"},'course_concept':{'topic_id':'VARCHAR','scope':"TEXT NOT NULL DEFAULT ''"},'prerequisite':{'details':"JSON NOT NULL DEFAULT '{}'"},'learner_state':{'mastery_level':'INTEGER NOT NULL DEFAULT 0','mastery_data':"JSON NOT NULL DEFAULT '{}'",'evidence_source':"VARCHAR NOT NULL DEFAULT 'unknown'"},'attempt':{'data':"JSON NOT NULL DEFAULT '{}'"}}
    with engine.begin() as conn:
        tables=inspect(conn).get_table_names()
        for table,fields in additions.items():
            if table not in tables:continue
            columns={x['name'] for x in inspect(conn).get_columns(table)}
            for name,sqltype in fields.items():
                if name not in columns:conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sqltype}'))
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.get(m.SchemaVersion,'mastery-v2'):return
        for link in db.scalars(select(m.CourseConcept)).all():
            if not link.topic_id:link.topic_id=str(uuid4())
        legacy_events=db.scalars(select(m.LearnerEvent).where(m.LearnerEvent.kind=='concept_completed').order_by(text('rowid'))).all()
        completed={x.concept_id for x in legacy_events}
        from .policy import local_date
        from datetime import datetime
        first_dates={}
        for e in legacy_events:
            try:first_dates.setdefault(e.concept_id,local_date(datetime.fromisoformat(e.created_at)))
            except ValueError:pass
        for cid in completed:
            if not db.get(m.LearnerState,('local_user',cid)):db.add(m.LearnerState(profile_id='local_user',concept_id=cid))
        db.flush()
        for state in db.scalars(select(m.LearnerState)).all():
            state.mastery_level={'unknown':0,'needs_review':1,'self_reported_familiar':3}.get(state.familiarity,0)
            state.evidence_source='legacy_explicit_self_report'
            state.mastery_data={'first_completed':state.concept_id in completed,'review_count':0,'doubt_count':0,'error_count':0,'source':'legacy_explicit_self_report','first_date':first_dates.get(state.concept_id),'base':True}
            db.add(m.LearnerEvent(id=str(uuid4()),operation_id='mastery-baseline:'+state.concept_id,concept_id=state.concept_id,kind='mastery_baseline',before={},after={'level':state.mastery_level,'data':state.mastery_data},created_at='migration'))
        db.add(m.SchemaVersion(id='mastery-v2'));db.commit()
