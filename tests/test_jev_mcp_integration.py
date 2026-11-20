"""Integration tests over the forecast server's stdio MCP interface."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from futuresim_agents.minimalHarnessAgent import mcp_server


QUESTION = {"qid": "atlas_001", "title": "Will Atlas launch?", "resolution_criteria": "A public launch counts."}
PASSAGE = "Atlas launched publicly today."
CORE_TOOLS = {"search_news", "submit_forecasts", "next_day"}


class JevMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.agent_dir = Path(temp.name)
        self.workspace = self.agent_dir / "workspace"
        self.workspace.mkdir()
        (self.agent_dir / "state.json").write_text(json.dumps({
            "current_date": "2026-01-01", "questions": [QUESTION], "next_day_returns_immediately": True,
        }))

    @asynccontextmanager
    async def session(self, *flags, api_key=""):
        """Start an isolated MCP process with explicit flags and the selected host credential."""
        params = StdioServerParameters(
            command=sys.executable,
            args=[mcp_server.__file__, "--workspace", str(self.workspace), "--internal-dir", str(self.agent_dir), *flags],
            env={"TYPESAFE_API_KEY": api_key},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            yield client

    async def test_baseline_and_explicit_filter(self):
        for flags, expected in [
            ((), CORE_TOOLS),
            (("--enable-jev-tool", "--enabled-tools", "submit_forecasts"), {"submit_forecasts"}),
        ]:
            with self.subTest(flags=flags):
                async with self.session(*flags) as client:
                    self.assertEqual({tool.name for tool in (await client.list_tools()).tools}, expected)

    async def test_enabled_errors_and_forecast_continuation(self):
        async with self.session("--enable-jev-tool", "--jev-model", "jev-test") as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertEqual(set(tools), CORE_TOOLS | {"analyze_evidence"})
            schema = tools["analyze_evidence"].inputSchema
            self.assertEqual(set(schema["properties"]), {"question_id", "evidence_text", "source_id"})
            self.assertEqual(set(schema["required"]), {"question_id", "evidence_text"})
            for qid, passage, message in [
                ("inactive", PASSAGE, "not active"),
                ("atlas_001", " ", "nonblank"),
                ("atlas_001", PASSAGE, "credentials"),
            ]:
                reply = await client.call_tool("analyze_evidence", {"question_id": qid, "evidence_text": passage})
                self.assertFalse(reply.isError)
                result = json.loads(reply.content[0].text)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["model"], "jev-test")
                self.assertIn(message, result["error"])
            await client.call_tool("submit_forecasts", {"question_id": "atlas_001", "outcomes": {"Yes": 0.7, "No": 0.3}})
            await client.call_tool("next_day", {})
        signal = json.loads((self.agent_dir / "signals/next_day_2026-01-01.json").read_text())
        self.assertEqual(signal["predictions"], [{"question_id": "atlas_001", "outcomes": {"Yes": 0.7, "No": 0.3}}])
        records = [json.loads(line) for line in (self.agent_dir / "jev_calls.jsonl").read_text().splitlines()]
        self.assertEqual([row["status"] for row in records], ["error"] * 3)
        self.assertEqual(records[-1]["simulation_date"], "2026-01-01")
        self.assertEqual(records[-1]["evidence_sha256"], hashlib.sha256(PASSAGE.encode()).hexdigest())

    @unittest.skipUnless(os.environ.get("TYPESAFE_API_KEY", "").strip(), "TYPESAFE_API_KEY is required")
    async def test_live_analysis_and_log(self):
        async with self.session("--enable-jev-tool", api_key=os.environ["TYPESAFE_API_KEY"]) as client:
            reply = await client.call_tool("analyze_evidence", {
                "question_id": "atlas_001", "evidence_text": PASSAGE, "source_id": "live-atlas",
            })
        self.assertFalse(reply.isError)
        result = json.loads(reply.content[0].text)
        self.assertEqual(result["status"], "success", result.get("error"))
        self.assertEqual(set(result["signals"]), {"relevance", "direction", "outcome_confirmed"})
        record = json.loads((self.agent_dir / "jev_calls.jsonl").read_text())
        for field in ("model", "signals", "usage", "source_id", "question_id", "status"):
            self.assertEqual(record[field], result[field])
        self.assertEqual(record["source_id"], "live-atlas")
        self.assertEqual(record["question_types"], ["score", "choice", "noul"])
        self.assertGreaterEqual(record["latency_ms"], 0)
