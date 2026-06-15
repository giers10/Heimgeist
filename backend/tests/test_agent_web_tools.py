import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.agent.registry import NativeToolProvider, ToolExecutionContext
from backend.agent.tools.web import FETCH_TEXT_LIMIT, register_web_tools


def context(registry):
    return ToolExecutionContext(
        run_id="run",
        workflow_id="workflow",
        node_id="fetch",
        session_id=None,
        selection_mode="explicit",
        explicit_user_action=True,
        emit=lambda *_args: asyncio.sleep(0),
        cancellation_event=asyncio.Event(),
        db_factory=lambda: None,
        registry=registry,
    )


class WebToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_accepts_search_result_batches_and_caps_page_text(self):
        registry = NativeToolProvider()
        register_web_tools(registry)
        urls = [{"url": f"https://example.test/{index}"} for index in range(16)]

        async def fake_fetch(url):
            return {
                "requested_url": url,
                "url": url,
                "title": "Example",
                "text": "x" * (FETCH_TEXT_LIMIT + 500),
            }

        with patch("backend.agent.tools.web.fetch_website_snapshot", new=AsyncMock(side_effect=fake_fetch)):
            result = await registry.call_tool(
                "heimgeist.web_fetch",
                {"url": None, "urls": urls, "maximum_pages": 6},
                context(registry),
            )

        self.assertEqual(len(result["pages"]), 6)
        self.assertTrue(all(len(page["cleaned_text"]) == FETCH_TEXT_LIMIT for page in result["pages"]))
