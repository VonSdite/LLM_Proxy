from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.presentation.app_factory import create_flask_app


class DataPlaneCorsTests(unittest.TestCase):
    def test_v1_options_preflight_returns_cors_headers(self) -> None:
        app = create_flask_app()
        client = app.test_client()

        response = client.options(
            "/v1/chat/completions",
            headers={
                "Origin": "app://obsidian.md",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type, x-api-key",
            },
        )

        self.assertEqual(204, response.status_code)
        self.assertEqual(
            "app://obsidian.md",
            response.headers.get("Access-Control-Allow-Origin"),
        )
        self.assertEqual(
            "authorization, content-type, x-api-key",
            response.headers.get("Access-Control-Allow-Headers"),
        )
        self.assertIn("POST", response.headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("OPTIONS", response.headers.get("Access-Control-Allow-Methods", ""))

    def test_v1_response_includes_cors_headers(self) -> None:
        app = create_flask_app()

        @app.get("/v1/models")
        def list_models():
            return {"object": "list", "data": []}

        response = app.test_client().get(
            "/v1/models",
            headers={"Origin": "app://obsidian.md"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "app://obsidian.md",
            response.headers.get("Access-Control-Allow-Origin"),
        )

    def test_control_plane_response_does_not_include_cors_headers(self) -> None:
        app = create_flask_app()

        @app.get("/api/users")
        def list_users():
            return {"users": []}

        response = app.test_client().get(
            "/api/users",
            headers={"Origin": "app://obsidian.md"},
        )

        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()
