
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .paths import database_path, sqlite_database_url

"""
Database utilities and configuration. This module defines the SQLAlchemy
engine, session factory and base class for models. It also contains a
lightweight migration helper used to evolve the schema over time. The
`ensure_sources_column` helper adds the JSON-backed columns used by chat
messages when they do not already exist.

The migration uses SQLite's `ALTER TABLE` syntax and therefore should
only run once on startup. It is safe to call repeatedly: when a column
already exists, the function will simply no-op.
"""

DATABASE_PATH = database_path()
DATABASE_URL = sqlite_database_url(DATABASE_PATH)

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass


def ensure_sources_column(engine):
    try:
        with engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(chat_messages)"))]
            if "sources_json" not in cols:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN sources_json TEXT DEFAULT '[]'"))
            if "attachments_json" not in cols:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN attachments_json TEXT DEFAULT '[]'"))
    except Exception as e:
        print("[db] ensure_sources_column error:", e)
