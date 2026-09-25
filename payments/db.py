"""SQLite connections are created, used and closed inside one synchronous worker."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def connect(path, timeout_ms=5000):
    db = sqlite3.connect(str(path), isolation_level=None, timeout=timeout_ms / 1000)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute(f'PRAGMA busy_timeout={int(timeout_ms)}')
    db.execute('PRAGMA synchronous=FULL')
    return db


def initialize(path):
    if str(path) == ':memory:':
        raise ValueError('Use a file-backed database; request connections cannot share :memory:.')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError(f'Unsupported schema version: {version}')
        if version == 0:
            schema = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
            db.executescript('BEGIN IMMEDIATE;\n' + schema + '\nCOMMIT;')
    finally:
        db.close()


@contextmanager
def transaction(db, write=False):
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def session(settings, write=False):
    db = connect(settings.database_path, settings.db_timeout_ms)
    try:
        with transaction(db, write=write):
            yield db
    finally:
        db.close()
