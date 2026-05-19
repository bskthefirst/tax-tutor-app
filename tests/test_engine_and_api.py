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
        os.environ.pop("TAX_TUTOR_DATABASE_URL", None)
        os.environ.pop("NEON_DATABASE_URL", None)
        os.environ["TAX_TUTOR_DISABLE_PROVIDER"] = "1"
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
            "completed_lessons",
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

    def test_exam_center_includes_new_modes(self) -> None:
        state = self.engine._default_state()  # pylint: disable=protected-access
        center = self.engine._build_exam_center(state, self.engine.lessons[0])  # pylint: disable=protected-access
        mode_names = {mode["exam_mode"] for mode in center["modes"]}
        self.assertIn("diagnostic_pretest", mode_names)
        self.assertIn("workpaper_drill", mode_names)
        self.assertIn("cumulative_timed", mode_names)

    def test_mistake_notebook_includes_taxonomy_counts(self) -> None:
        lesson = self.engine.lessons[0]
        state = self.engine._default_state()  # pylint: disable=protected-access
        state["mistake_notebook"] = [
            {
                "entry_id": "m1",
                "lesson_id": lesson.lesson_id,
                "reopen_lesson_id": lesson.lesson_id,
                "lesson_title": lesson.title,
                "chapter_number": lesson.chapter_number,
                "prompt": "Sample prompt 1",
                "selected_option": "A",
                "correct_option": "B",
                "why_selected_wrong": "Reason 1",
                "why_correct_right": "Reason 2",
                "taxonomy": "concept_confusion",
                "resolved": False,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "entry_id": "m2",
                "lesson_id": lesson.lesson_id,
                "reopen_lesson_id": lesson.lesson_id,
                "lesson_title": lesson.title,
                "chapter_number": lesson.chapter_number,
                "prompt": "Sample prompt 2",
                "selected_option": "C",
                "correct_option": "D",
                "why_selected_wrong": "Reason 3",
                "why_correct_right": "Reason 4",
                "taxonomy": "calculation_error",
                "resolved": False,
                "created_at": "2026-01-02T00:00:00+00:00",
            },
        ]
        notebook = self.engine._build_mistake_notebook(state)  # pylint: disable=protected-access
        self.assertEqual(notebook["unresolved_count"], 2)
        self.assertEqual(notebook["taxonomy_counts"].get("concept_confusion"), 1)
        self.assertEqual(notebook["taxonomy_counts"].get("calculation_error"), 1)
        self.assertTrue(all("taxonomy" in item for item in notebook["items"]))

    def test_submit_quiz_auto_saves_lesson_completion(self) -> None:
        lesson = self.engine.lessons[0]
        state = self.engine._default_state()  # pylint: disable=protected-access
        state["current_lesson_id"] = lesson.lesson_id
        state["last_card"] = {
            "lesson_id": lesson.lesson_id,
            "coverage": {},
            "flashcards": [],
            "quiz_questions": [
                {
                    "question_id": "q1",
                    "question_number": 1,
                    "prompt": "Q1",
                    "correct_option": "A",
                    "study_answer": "Rule summary.",
                    "options": [
                        {"label": "A", "text": "Right", "why": "Matches the rule."},
                        {"label": "B", "text": "Wrong", "why": "Does not match."},
                        {"label": "C", "text": "Wrong", "why": "Does not match."},
                        {"label": "D", "text": "Wrong", "why": "Does not match."},
                        {"label": "E", "text": "Wrong", "why": "Does not match."},
                    ],
                }
            ],
        }
        self.engine._save_state(state)  # pylint: disable=protected-access
        result = self.engine.handle_action("submit_quiz", {"answers": ["A"]})
        completed = result["state"]["completed_lesson_count"]
        self.assertGreaterEqual(completed, 1)
        self.assertIn(lesson.lesson_id, self.engine._load_state()["completed_lessons"])  # pylint: disable=protected-access
        self.assertIn("cumulative_mini_quiz", result["card"])

    def test_hydrate_state_restores_completion(self) -> None:
        lesson = self.engine.lessons[0]
        payload = {
            "current_lesson_id": lesson.lesson_id,
            "completed_lessons": [lesson.lesson_id],
            "last_card": None,
            "flashcards": {},
            "lesson_performance": {},
            "mistake_notebook": [],
            "weekly_goal_lessons": 2,
            "midterm_mode": {"enabled": False, "start_chapter": 1, "end_chapter": 25},
            "updated_at": "2026-05-16T20:00:00+00:00",
        }
        result = self.engine.handle_action("hydrate_state", {"state": payload})
        self.assertGreaterEqual(result["state"]["completed_lesson_count"], 1)
        self.assertIn(lesson.lesson_id, self.engine._load_state()["completed_lessons"])  # pylint: disable=protected-access

    def test_complete_and_continue_requires_recall_sentence(self) -> None:
        lesson = self.engine.lessons[0]
        self.engine.handle_action("open_lesson", {"lesson_id": lesson.lesson_id})
        with self.assertRaises(ValueError):
            self.engine.handle_action("complete_and_continue", {"recall_text": "too short"})
        result = self.engine.handle_action(
            "complete_and_continue",
            {"recall_text": "The rule applies when the taxable trigger is present."},
        )
        self.assertGreaterEqual(result["state"]["completed_lesson_count"], 1)

    def test_client_scoped_state_isolated(self) -> None:
        lesson = self.engine.lessons[0]
        client_a = "client_a"
        client_b = "client_b"
        self.engine.handle_action("open_lesson", {"lesson_id": lesson.lesson_id}, client_id=client_a)
        self.engine.handle_action(
            "complete_and_continue",
            {"recall_text": "This rule applies when the triggering tax fact is present."},
            client_id=client_a,
        )
        payload_a = self.engine.bootstrap(client_id=client_a)
        payload_b = self.engine.bootstrap(client_id=client_b)
        self.assertIn(lesson.lesson_id, payload_a["completed_lessons"])
        self.assertNotIn(lesson.lesson_id, payload_b["completed_lessons"])


if __name__ == "__main__":
    unittest.main()
