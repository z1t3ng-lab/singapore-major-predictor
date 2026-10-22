"""Serialize API operations across local app instances, including database restore."""
import asyncio
import fcntl
from contextlib import asynccontextmanager
from . import database

@asynccontextmanager
async def workspace_lock():
    path = database.DB_PATH.with_suffix('.sqlite3.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
