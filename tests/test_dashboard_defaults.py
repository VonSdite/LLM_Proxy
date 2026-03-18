import unittest
from pathlib import Path

from flask import Flask, render_template


class DashboardDefaultDateRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        presentation_root = Path(__file__).resolve().parents[1] / "src" / "presentation"
        self.app = Flask(
            __name__,
            template_folder=str(presentation_root / "templates"),
            static_folder=str(presentation_root / "static"),
            static_url_path="/static",
        )

    def render_dashboard(self) -> str:
        with self.app.app_context():
            return render_template(
                "index.html",
                active_page="index",
                current_username="",
                auth_enabled=False,
            )

    def test_dashboard_shows_today_preset_button(self) -> None:
        html = self.render_dashboard()

        self.assertIn('data-date-preset="today"', html)
        self.assertIn("今天</button>", html)
        self.assertNotIn("近3天</button>", html)

    def test_dashboard_defaults_date_range_to_today(self) -> None:
        html = self.render_dashboard()

        self.assertIn("const defaultRange = buildDatePresetRange('today');", html)
        self.assertNotIn("const defaultRange = buildDatePresetRange('last3');", html)


if __name__ == "__main__":
    unittest.main()
