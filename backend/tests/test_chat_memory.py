from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.chat_memory import (
    build_chat_memory_context,
    ensure_chat_memory_schema,
    sync_all_chat_memory,
    sync_chat_memory_for_session,
)
from backend.database import Base
from backend.models import ChatMessage, ChatSession


class ChatMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'memory.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        ensure_chat_memory_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _turn(self, db, session_id, title, user_content, assistant_content):
        session = ChatSession(session_id=session_id, name=title)
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_pk=session.id, role="user", content=user_content))
        db.flush()
        db.add(ChatMessage(session_pk=session.id, role="assistant", content=assistant_content))
        db.flush()
        return session

    def test_retrieves_completed_past_turns(self):
        db = self.Session()
        try:
            self._turn(
                db,
                "past",
                "Tauri Packaging",
                "What did we decide about mac packaging?",
                "Use the PyInstaller backend sidecar and keep app data outside the bundle.",
            )
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(db, "remind me about the backend sidecar packaging plan")
        finally:
            db.close()

        self.assertIn("chat_memory_context", context["context_block"])
        self.assertIn("PyInstaller backend sidecar", context["context_block"])
        self.assertEqual(context["sources"][0]["source_session_id"], "past")

    def test_excludes_active_session(self):
        db = self.Session()
        try:
            self._turn(db, "current", "Current", "secret kiwi topic", "Only the current chat mentions kiwi.")
            db.commit()
            sync_all_chat_memory(db)

            excluded = build_chat_memory_context(db, "kiwi topic", exclude_session_id="current")
            included = build_chat_memory_context(db, "kiwi topic")
        finally:
            db.close()

        self.assertEqual(excluded["context_block"], "")
        self.assertIn("kiwi", included["context_block"])

    def test_resync_removes_stale_turn_content(self):
        db = self.Session()
        try:
            session = self._turn(db, "past", "Old Decision", "backend sidecar", "Keep using the old sidecar plan.")
            db.commit()
            sync_all_chat_memory(db)

            assistant = db.query(ChatMessage).filter(ChatMessage.role == "assistant").one()
            assistant.content = "This turn now discusses an unrelated renderer detail."
            db.commit()
            sync_chat_memory_for_session(db, session.session_id)

            context = build_chat_memory_context(db, "old sidecar plan")
        finally:
            db.close()

        self.assertEqual(context["context_block"], "")
