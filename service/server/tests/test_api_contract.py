"""Contract tests for the platform surface agents talk to.

Covers the health probe, the API 404 behaviour behind the SPA fallback, and the
signal payload shapes documented in ``skills/ai4trade/SKILL.md``.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import database
from routes import create_app
from routes_shared import utc_now_iso_z


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        database.DATABASE_URL = ""
        database._SQLITE_DB_PATH = os.path.join(self.tmp.name, "test.db")
        database.init_database()
        self.agent_id = self._create_agent()
        self.client = TestClient(create_app())
        self.headers = {"Authorization": "Bearer token-contract"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_agent(self) -> int:
        now = utc_now_iso_z()
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agents (name, token, points, cash, created_at, updated_at)
            VALUES ('contract-agent', 'token-contract', 0, 100000.0, ?, ?)
            """,
            (now, now),
        )
        agent_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return agent_id

    def test_health_endpoint_reports_backend(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"]["backend"], "sqlite")
        self.assertIn("cache", payload)

    def test_unknown_api_path_returns_404(self) -> None:
        response = self.client.get("/api/definitely-not-a-route")
        self.assertEqual(response.status_code, 404, response.text)

    def test_unknown_frontend_path_falls_back_to_spa(self) -> None:
        response = self.client.get("/leaderboard")
        self.assertEqual(response.status_code, 200, response.text)

    def test_strategy_accepts_documented_list_payload(self) -> None:
        response = self.client.post(
            "/api/signals/strategy",
            headers=self.headers,
            json={
                "market": "crypto",
                "title": "BTC Breaking Out",
                "content": "Analysis: BTC may break out this weekend.",
                "symbols": ["BTC", "ETH"],
                "tags": ["bitcoin", "breakout"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        feed = self.client.get("/api/signals/feed?limit=5").json()
        signal = feed["signals"][0]
        self.assertEqual(signal["symbols"], ["BTC", "ETH"])
        self.assertEqual(signal["tags"], ["bitcoin", "breakout"])

    def test_strategy_still_accepts_comma_separated_payload(self) -> None:
        response = self.client.post(
            "/api/signals/strategy",
            headers=self.headers,
            json={
                "market": "crypto",
                "title": "SOL rotation",
                "content": "Rotating part of the book into SOL.",
                "symbols": "SOL, JUP",
                "tags": "solana",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        feed = self.client.get("/api/signals/feed?limit=5").json()
        signal = feed["signals"][0]
        self.assertEqual(signal["symbols"], ["SOL", "JUP"])
        self.assertEqual(signal["tags"], ["solana"])

    def test_discussion_accepts_list_tags(self) -> None:
        response = self.client.post(
            "/api/signals/discussion",
            headers=self.headers,
            json={
                "market": "crypto",
                "title": "Thoughts on the BTC trend",
                "content": "Sharing a view on the current BTC structure.",
                "tags": ["bitcoin", "opinion"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
