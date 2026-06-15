import unittest
from types import SimpleNamespace

from backend.agent.tools.chat import _drop_superseded_trailing_user_rows


class ChatToolTests(unittest.TestCase):
    def test_drops_unanswered_trailing_user_messages_except_latest(self):
        rows = [
            SimpleNamespace(role="user", content="first question"),
            SimpleNamespace(role="assistant", content="first answer"),
            SimpleNamespace(role="user", content="interrupted follow-up"),
            SimpleNamespace(role="user", content="latest prompt"),
        ]

        result = _drop_superseded_trailing_user_rows(rows)

        self.assertEqual([row.content for row in result], ["first question", "first answer", "latest prompt"])

    def test_keeps_normal_alternating_history(self):
        rows = [
            SimpleNamespace(role="user", content="first question"),
            SimpleNamespace(role="assistant", content="first answer"),
            SimpleNamespace(role="user", content="latest prompt"),
        ]

        self.assertIs(_drop_superseded_trailing_user_rows(rows), rows)

