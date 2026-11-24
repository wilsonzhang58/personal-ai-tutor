from contextlib import contextmanager
import fcntl,hashlib
from pathlib import Path
from threading import RLock
from collections import defaultdict
LOCKS=defaultdict(RLock)
@contextmanager
def task_lock(key):
    directory=Path(__file__).resolve().parents[2]/'data/private/locks';directory.mkdir(parents=True,exist_ok=True)
    with LOCKS[key], (directory/hashlib.sha256(key.encode()).hexdigest()).open('a') as file:
        fcntl.flock(file,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(file,fcntl.LOCK_UN)
