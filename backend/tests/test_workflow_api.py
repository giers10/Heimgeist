from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent import api
from backend.agent.models import WorkflowDefinition, WorkflowRun
from backend.agent.schemas import WorkflowRunRequest
from backend.agent.workflows.builtins import seed_builtin_workflows
from backend.database import Base
from backend.models import ChatMessage, ChatSession


class WorkflowApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'api.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        seed_builtin_workflows(db)
        direct = db.query(WorkflowDefinition).filter_by(slug="input-output").one()
        chat = ChatSession(session_id="regenerate-chat")
        db.add(chat)
        db.flush()
        user = ChatMessage(session_pk=chat.id, role="user", content="original")
        db.add(user)
        db.flush()
        db.add(ChatMessage(
            session_pk=chat.id,
            role="assistant",
            content="old answer",
            workflow_id=direct.id,
            workflow_revision_id=direct.current_revision_id,
        ))
        db.commit()
        self.user_message_id = user.message_id
        self.direct_revision_id = direct.current_revision_id
        db.close()

    async def asyncTearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    async def test_regeneration_reuses_user_row_and_prunes_old_answer(self):
        started = []
        fake_runtime = SimpleNamespace(start=started.append)
        request = WorkflowRunRequest(
            session_id="regenerate-chat",
            message="edited",
            model="test-model",
            selection_mode="direct",
            workflow_revision_id=self.direct_revision_id,
            regenerate_index=0,
        )
        with patch.object(api, "SessionLocal", self.Session), patch.object(api, "runtime", fake_runtime):
            response = await api.create_workflow_run(request)

        self.assertEqual(started, [response["run_id"]])
        db = self.Session()
        rows = db.query(ChatMessage).order_by(ChatMessage.id.asc()).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].message_id, self.user_message_id)
        self.assertEqual(rows[0].content, "edited")
        run = db.query(WorkflowRun).filter_by(id=response["run_id"]).one()
        self.assertEqual(run.user_message_id, self.user_message_id)
        db.close()
