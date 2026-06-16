from pathlib import Path
import asyncio
from datetime import datetime, timedelta
import json
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
from backend.agent.tools.memory import chat_memory_search_handler
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

    def _turn_with_sources(self, db, session_id, title, user_content, assistant_content, sources):
        session = ChatSession(session_id=session_id, name=title)
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_pk=session.id, role="user", content=user_content))
        db.flush()
        db.add(ChatMessage(
            session_pk=session.id,
            role="assistant",
            content=assistant_content,
            sources_json=json.dumps(sources),
        ))
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

    def test_recent_mode_returns_most_recent_previous_session(self):
        db = self.Session()
        try:
            base = datetime(2026, 1, 1, 12, 0, 0)
            self._turn(db, "older", "Weather", "what is the weather?", "It is rainy.", created_at=base)
            self._turn(db, "previous", "Crowley", "tell me about Crowley", "The previous chat discussed Crowley.", created_at=base + timedelta(minutes=1))
            current = ChatSession(session_id="current", name="Letztes Gespräch", created_at=base + timedelta(minutes=2))
            db.add(current)
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(
                db,
                "worum ging es im letzten gespräch?",
                exclude_session_id="current",
                mode="recent",
            )
        finally:
            db.close()

        self.assertIn("Crowley", context["context_block"])
        self.assertNotIn("rainy", context["context_block"])

    def test_search_mode_uses_supplied_query_terms(self):
        db = self.Session()
        try:
            self._turn(db, "song", "Identify this song", "what is this song?", "We discussed a track by an electronic artist.")
            self._turn(db, "lied", "Lied: Hintergründe suchen", "suche hintergründe zu diesem lied", "Das Lied wurde im Kontext einer Band besprochen.")
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(db, "song lied")
        finally:
            db.close()

        self.assertIn("song", context["context_block"].casefold())
        self.assertIn("lied", context["context_block"].casefold())

    def test_chat_memory_tool_returns_context_block_and_sources(self):
        db = self.Session()
        try:
            self._turn(
                db,
                "past",
                "Backend Sidecar",
                "What was the sidecar plan?",
                "Use PyInstaller for the backend sidecar and keep runtime data outside the app bundle.",
            )
            db.commit()
            sync_all_chat_memory(db)
        finally:
            db.close()

        context = type("Context", (), {
            "session_id": "current",
            "db_factory": self.Session,
        })()
        result = asyncio.run(chat_memory_search_handler({
            "prompt": "backend sidecar runtime data",
            "top_k": 3,
            "context_character_budget": 4000,
            "exclude_current_session": True,
        }, context))

        self.assertIn("PyInstaller", result["context_block"])
        self.assertEqual(result["sources"][0]["type"], "chat_memory")
        self.assertEqual(result["hits"][0]["session_id"], "past")

    def test_memory_only_answers_are_not_reindexed(self):
        db = self.Session()
        try:
            self._turn_with_sources(
                db,
                "poison",
                "Backfire Basics",
                "erzähl mir über backfire basics",
                "Backfire Basics is a hallucinated project description.",
                [{"type": "chat_memory", "source_session_id": "older", "source_message_id": "m1"}],
            )
            db.commit()
            sync_all_chat_memory(db)

            context = build_chat_memory_context(db, "backfire basics")
        finally:
            db.close()

        self.assertEqual(context["context_block"], "")
