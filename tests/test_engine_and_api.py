from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = APP_ROOT.parent / "textlayer-work" / "tutor-assets"


class EngineAndApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_data_dir = tempfile.TemporaryDirectory(prefix="tax-tutor-tests-")
        os.environ["TAX_TUTOR_ASSETS_ROOT"] = str(ASSETS_ROOT)
        os.environ["TAX_TUTOR_DATA_ROOT"] = cls._temp_data_dir.name
        os.environ.pop("PORT", None)
        os.environ.pop("HOST", None)

        from engine import TaxTutorEngine  # pylint: disable=import-outside-toplevel
        import app  # pylint: disable=import-outside-toplevel

        cls.TaxTutorEngine = TaxTutorEngine
        cls.app = app
        cls.engine = TaxTutorEngine(APP_ROOT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp_data_dir.cleanup()

    def test_engine_boot(self) -> None:
        self.assertEqual(len(self.engine.chapter_index), 25)
        self.assertGreater(len(self.engine.lessons), 0)
        self.assertGreater(len(self.engine.chunks), 0)

    def test_curriculum_excludes_backmatter_headings(self) -> None:
        titles = [lesson.title for lesson in self.engine.lessons]
        self.assertFalse(any(len(title) == 1 and title.isalpha() for title in titles))
        self.assertFalse(any(title.startswith("Return to ") for title in titles))
        self.assertFalse(any("Code Index" in title for title in titles))
        self.assertFalse(any("Subject Index" in title for title in titles))
        self.assertFalse(any("ADDITIONAL STUDENT RESOURCES" in title for title in titles))

    def test_bootstrap_contains_required_keys(self) -> None:
        payload = self.engine.bootstrap()
        required = {
            "book_title",
            "chapter_count",
            "lesson_count",
            "completed_lesson_count",
            "current_lesson",
            "chapters",
            "weekly_plan",
            "today_assignment",
            "up_next",
        }
        self.assertTrue(required.issubset(payload.keys()))

    def test_local_quiz_grading(self) -> None:
        card = {
            "quiz_questions": [
                {
                    "question_number": 1,
                    "prompt": "Q1",
                    "correct_option": "A",
                    "study_answer": "tax base means amount subject to tax",
                    "options": [{"label": "A", "text": "a"}, {"label": "B", "text": "b"}],
                },
                {
                    "question_number": 2,
                    "prompt": "Q2",
                    "correct_option": "C",
                    "study_answer": "marginal rate applies to next dollar",
                    "options": [{"label": "C", "text": "c"}, {"label": "D", "text": "d"}],
                },
            ]
        }
        result = self.engine._grade_quiz_locally(card, ["A", "D"])  # pylint: disable=protected-access
        self.assertEqual(result["total_questions"], 2)
        self.assertEqual(result["correct_count"], 1)
        self.assertEqual(len(result["question_feedback"]), 2)

    def test_invalid_action_returns_400(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.app.TaxTutorHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            body = json.dumps({"action": "invalid_action_name"})
            conn.request("POST", "/api/action", body=body, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 400)
            self.assertIn("Unsupported action", data.get("error", ""))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
