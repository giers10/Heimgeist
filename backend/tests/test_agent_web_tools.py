import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from backend.agent.registry import NativeToolProvider, ToolExecutionContext
from backend.agent.tools.web import FETCH_TEXT_LIMIT, register_web_tools
from backend.ollama_client import OllamaChatResult


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
    async def test_generate_queries_returns_llm_queries_without_topic_parser(self):
        registry = NativeToolProvider()
        register_web_tools(registry)

        result_payload = {"queries": [" latest ", "top stories", "latest Iran news headlines", "latest"]}
        with patch(
            "backend.agent.tools.web.chat_typed",
            new=AsyncMock(return_value=OllamaChatResult(content=json.dumps(result_payload))),
        ):
            result = await registry.call_tool(
                "heimgeist.web_generate_queries",
                {"prompt": "what's the news in iran today", "model": "model", "messages": []},
                context(registry),
            )

        queries = result["queries"]
        self.assertEqual(queries, ["latest", "top stories", "latest Iran news headlines"])

    async def test_search_filters_dictionary_noise_for_current_lookup(self):
        registry = NativeToolProvider()
        register_web_tools(registry)

        async def fake_search(_client, _query, **_kwargs):
            return [
                {"url": "https://dict.leo.org/englisch-deutsch/latest", "title": "latest - Deutsch-Uebersetzung", "engine": "bing"},
                {"url": "https://www.reuters.com/world/middle-east/iran-example", "title": "Iran latest news", "engine": "bing"},
                {"url": "https://consent.google.com/ml?continue=https://news.google.com/topics/iran", "title": "Before you continue", "engine": "bing"},
            ]

        with patch("backend.agent.tools.web.searx_search", new=AsyncMock(side_effect=fake_search)):
            result = await registry.call_tool(
                "heimgeist.web_search",
                {
                    "query": None,
                    "queries": ["latest Iran news headlines"],
                    "engines": [],
                    "maximum_results": 8,
                    "searx_url": "http://127.0.0.1:8888",
                },
                context(registry),
            )

        urls = [item["url"] for item in result["results"]]
        self.assertEqual(urls, ["https://www.reuters.com/world/middle-east/iran-example"])

    async def test_search_does_not_require_parsed_topic_terms(self):
        registry = NativeToolProvider()
        register_web_tools(registry)

        async def fake_search(_client, _query, **_kwargs):
            return [
                {"url": "https://www.aljazeera.com/where/iran/", "title": "Iran news", "engine": "bing"},
                {"url": "https://www.bbc.com/news/world-middle-east", "title": "Middle East latest", "engine": "bing"},
            ]

        with patch("backend.agent.tools.web.searx_search", new=AsyncMock(side_effect=fake_search)):
            result = await registry.call_tool(
                "heimgeist.web_search",
                {
                    "query": None,
                    "queries": ["what happened in iran today"],
                    "engines": [],
                    "maximum_results": 8,
                    "searx_url": "http://127.0.0.1:8888",
                },
                context(registry),
            )

        urls = [item["url"] for item in result["results"]]
        self.assertEqual(urls, ["https://www.aljazeera.com/where/iran/", "https://www.bbc.com/news/world-middle-east"])

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

    async def test_fetch_filters_consent_pages_after_redirects(self):
        registry = NativeToolProvider()
        register_web_tools(registry)

        async def fake_fetch(url):
            return {
                "requested_url": url,
                "url": "https://consent.google.com/ml?continue=https://news.google.com/topics/iran",
                "title": "Before you continue",
                "text": "Google privacy controls",
            }

        with patch("backend.agent.tools.web.fetch_website_snapshot", new=AsyncMock(side_effect=fake_fetch)):
            result = await registry.call_tool(
                "heimgeist.web_fetch",
                {"url": None, "urls": [{"url": "https://news.google.com/topics/iran"}], "maximum_pages": 1},
                context(registry),
            )

        self.assertEqual(result["pages"][0]["error"], "filtered_noise_page")
        self.assertEqual(result["pages"][0]["cleaned_text"], "")

    async def test_rerank_filters_pages_without_prompt_topic(self):
        registry = NativeToolProvider()
        register_web_tools(registry)
        pages = [
            {
                "requested_url": "https://de.langenscheidt.com/englisch-deutsch/latest",
                "canonical_url": "https://de.langenscheidt.com/englisch-deutsch/latest",
                "title": "latest - Deutsch-Uebersetzung",
                "cleaned_text": "latest means occurring most recently",
                "error": None,
            },
            {
                "requested_url": "https://www.reuters.com/world/middle-east/iran-example",
                "canonical_url": "https://www.reuters.com/world/middle-east/iran-example",
                "title": "Iran latest news",
                "cleaned_text": "Iran officials and regional sources reported new developments today.",
                "error": None,
            },
        ]

        ranked = [
            ("https://de.langenscheidt.com/englisch-deutsch/latest", "latest means occurring most recently", 99),
            ("https://www.reuters.com/world/middle-east/iran-example", "Iran officials reported new developments today.", 60),
        ]
        with patch("backend.agent.tools.web.rerank", new=AsyncMock(return_value=ranked)):
            result = await registry.call_tool(
                "heimgeist.web_rerank",
                {
                    "prompt": "what's the news in iran today",
                    "pages": pages,
                    "model": "model",
                    "rerank_model": None,
                    "context_excerpt": "",
                    "maximum_results": 6,
                    "minimum_score": 55,
                },
                context(registry),
            )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["url"], "https://www.reuters.com/world/middle-east/iran-example")
