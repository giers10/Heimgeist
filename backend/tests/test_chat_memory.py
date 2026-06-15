from pathlib import Path
from datetime import datetime, timedelta
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

    def _turn(self, db, session_id, title, user_content, assistant_content, created_at=None):
        created_at = created_at or datetime.utcnow()
        session = ChatSession(session_id=session_id, name=title, created_at=created_at)
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_pk=session.id, role="user", content=user_content, created_at=created_at))
        db.flush()
        db.add(ChatMessage(session_pk=session.id, role="assistant", content=assistant_content, created_at=created_at + timedelta(seconds=1)))
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
            session = self._turn(db, "past", "Old Decision", "deployment detail", "Keep using the banana corridor plan.")
            db.commit()
            sync_all_chat_memory(db)

            assistant = db.query(ChatMessage).filter(ChatMessage.role == "assistant").one()
            assistant.content = "This turn now discusses an unrelated renderer detail."
            db.commit()
            sync_chat_memory_for_session(db, session.session_id)

            context = build_chat_memory_context(db, "banana corridor plan")
        finally:
            db.close()

        self.assertEqual(context["context_block"], "")

    def test_generic_prompt_words_do_not_create_false_memory_hits(self):
        db = self.Session()
        try:
            self._turn(
                db,
                "story",
                "Test Conversation Start",
                "tell me a long story",
                "The village of Eldoria sat beneath a whispering forest canopy.",
            )
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(db, "tell me about thelama and crowley")
        finally:
            db.close()

        self.assertEqual(context["context_block"], "")

    def test_previous_conversation_uses_recency_not_semantic_overlap(self):
        db = self.Session()
        try:
            base = datetime(2026, 1, 1, 12, 0, 0)
            self._turn(db, "older", "Weather", "what is the weather?", "It is rainy.", created_at=base)
            self._turn(db, "previous", "Crowley", "tell me about Crowley", "The previous chat discussed Crowley.", created_at=base + timedelta(minutes=1))
            current = ChatSession(session_id="current", name="Letztes Gespräch", created_at=base + timedelta(minutes=2))
            db.add(current)
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(db, "worum ging es im letzten gespräch?", exclude_session_id="current")
        finally:
            db.close()

        self.assertIn("Crowley", context["context_block"])
        self.assertNotIn("rainy", context["context_block"])

    def test_music_query_matches_song_and_lied_history(self):
        db = self.Session()
        try:
            self._turn(db, "song", "Identify this song", "what is this song?", "We discussed a track by an electronic artist.")
            self._turn(db, "lied", "Lied: Hintergründe suchen", "suche hintergründe zu diesem lied", "Das Lied wurde im Kontext einer Band besprochen.")
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(db, "list all the music we talked about")
        finally:
            db.close()

        self.assertIn("song", context["context_block"].casefold())
        self.assertIn("lied", context["context_block"].casefold())
