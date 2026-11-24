import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from .config import PROJECT_ROOT
from sqlalchemy.orm import DeclarativeBase, sessionmaker

class Base(DeclarativeBase):
    pass

def database_url():
    url=make_url(os.getenv('TUTOR_DB_URL') or f'sqlite:///{PROJECT_ROOT / "data" / "tutor.db"}')
    if url.drivername=='sqlite' and url.database and url.database!=':memory:':
        path=Path(url.database).expanduser()
        if not path.is_absolute():path=PROJECT_ROOT/path
        path.parent.mkdir(parents=True,exist_ok=True)
        url=url.set(database=str(path))
    return url

engine = create_engine(database_url(), connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def get_db():
    with SessionLocal() as session:
        yield session
