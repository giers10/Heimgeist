import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.agent.tools.chat import _drop_superseded_trailing_user_rows, chat_handler


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


class ChatToolAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_handler_returns_context_fallback_when_model_fails(self):
        async def emit(_event_type, _payload):
            return None

        async def failing_stream(*_args, **_kwargs):
            raise RuntimeError("model returned 502")
            if False:
                yield None

        context = SimpleNamespace(
            session_id=None,
            db_factory=lambda: None,
            emit=emit,
            cancellation_event=asyncio.Event(),
            show_thinking=False,
        )
        arguments = {
            "model": "broken-model",
            "messages": [{"role": "user", "content": "weather?"}],
            "context_blocks": [{
                "context_block": "<websearch_context>Weather context</websearch_context>",
                "sources": [{
                    "type": "web",
                    "title": "Schwäbisch Gmünd Weather",
                    "snippet": "Sunny intervals and light wind.",
                    "url": "https://weather.example/schwaebisch-gmuend",
                }],
            }],
            "generation_options": {},
            "reasoning": False,
        }

        with patch("backend.agent.tools.chat.chat_stream_typed", failing_stream):
            result = await chat_handler(arguments, context)

        self.assertIn("selected chat model failed", result["content"])
        self.assertIn("Schwäbisch Gmünd Weather", result["content"])
        self.assertIn("https://weather.example/schwaebisch-gmuend", result["content"])
        self.assertEqual(result["sources"][0]["url"], "https://weather.example/schwaebisch-gmuend")
