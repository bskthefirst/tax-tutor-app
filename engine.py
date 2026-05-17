from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import ssl
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import certifi
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


CACHE_VERSION = "v7"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PSEUDO_EXAMPLE_RE = re.compile(r"^-+\s*Example\s*-+\s*(\d+-\d+)\s*$", re.IGNORECASE)
PDF_PAGE_RE = re.compile(r"PDF page (\d+)")
PAGE_HEADING_RE = re.compile(r"^## (?:PDF page \d+|Page [^(]+\((?:PDF page \d+)\))$")
FRONT_MATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
TOKEN_RE = re.compile(r"[a-z0-9$%.-]+")
QUIZ_OPTION_LABELS = ["A", "B", "C", "D", "E"]

SKIP_HEADINGS = {
    "LEARNING OBJECTIVES",
    "STORYLINE SUMMARY",
    "THE KEY FACTS",
    "DIGITAL EDITION",
}
REVIEW_HEADINGS = {"CONCLUSION", "SUMMARY"}
PRACTICE_HEADINGS = {"DISCUSSION QUESTIONS", "PROBLEMS", "AI/CRITICAL THINKING QUESTIONS"}
SKIP_SUBHEADINGS = {"DEMOCRATS"}
LOCAL_GRADE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "by",
    "for",
    "from",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "what",
    "when",
    "which",
    "why",
    "with",
}


@dataclass
class Lesson:
    lesson_id: str
    chapter_number: int
    chapter_title: str
    order_index: int
    lesson_kind: str
    title: str
    learning_goal: str
    retrieval_query: str
    anchor_heading: str | None = None
    parent_heading: str | None = None
    objective_code: str | None = None
    start_pdf_page: int | None = None
    end_pdf_page: int | None = None


class TaxTutorEngine:
    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root
        default_assets_root = app_root.parent / "textlayer-work" / "tutor-assets"
        assets_override = os.environ.get("TAX_TUTOR_ASSETS_ROOT", "").strip()
        self.assets_root = Path(assets_override).expanduser() if assets_override else default_assets_root
        data_override = os.environ.get("TAX_TUTOR_DATA_ROOT", "").strip()
        self.data_root = Path(data_override).expanduser() if data_override else (app_root / "data")
        self.cache_root = self.data_root / "cache"
        self.model_workdir = self.data_root / "codex-empty-workdir"
        self.state_path = self.data_root / "study_state.json"
        self.lesson_schema_path = self.data_root / "lesson_response_schema.json"
        self.grade_schema_path = self.data_root / "grade_response_schema.json"
        self.prompt_path = self.assets_root / "prompt" / "tax-tutor-system-prompt.md"
        self.chapter_index_path = self.assets_root / "chapter_index.json"
        self.chunks_path = self.assets_root / "taxation-2025-chunks.jsonl"
        self._validate_assets()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.model_workdir.mkdir(parents=True, exist_ok=True)
        self._write_response_schemas()

        self.system_prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        self.chapter_index = json.loads(self.chapter_index_path.read_text(encoding="utf-8"))
        self.max_chapter_number = max(chapter["chapter_number"] for chapter in self.chapter_index)
        self.chunks = self._load_chunks()
        self.chunk_lookup = {chunk["chunk_id"]: chunk for chunk in self.chunks}
        self.lessons = self._build_curriculum()
        self.lesson_lookup = {lesson.lesson_id: lesson for lesson in self.lessons}
        self.lesson_ids = [lesson.lesson_id for lesson in self.lessons]
        self.chapter_lessons: dict[int, list[Lesson]] = {}
        self.lesson_positions: dict[str, tuple[int, int]] = {}
        self.search_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for chapter in self.chapter_index:
            chapter_lessons = [lesson for lesson in self.lessons if lesson.chapter_number == chapter["chapter_number"]]
            self.chapter_lessons[chapter["chapter_number"]] = chapter_lessons
            total = len(chapter_lessons)
            for index, lesson in enumerate(chapter_lessons, start=1):
                self.lesson_positions[lesson.lesson_id] = (index, total)
        self._build_retrieval_index()
        self.lock = threading.Lock()
        self.warm_lock = threading.Lock()
        self.warm_inflight: set[str] = set()
        self.warm_queue: list[tuple[Lesson, str, str]] = []
        self.warm_event = threading.Event()
        self.warm_worker_started = False
        self.initial_warm_started = False
        self._start_warm_worker()

    def _validate_assets(self) -> None:
        required_paths = [
            self.assets_root,
            self.prompt_path,
            self.chapter_index_path,
            self.chunks_path,
            self.assets_root / "chapters",
        ]
        missing = [str(path) for path in required_paths if not path.exists()]
        if missing:
            message = [
                "Tax Tutor assets were not found.",
                f"Expected assets root: {self.assets_root}",
                "Set TAX_TUTOR_ASSETS_ROOT to your tutor-assets directory.",
                "Missing paths:",
                *[f"- {item}" for item in missing],
            ]
            raise RuntimeError("\n".join(message))

    def _load_chunks(self) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        with self.chunks_path.open(encoding="utf-8") as handle:
            for line in handle:
                chunk = json.loads(line)
                chunk["search_text"] = "\n".join(
                    [
                        chunk["chapter_title"],
                        " ".join(chunk.get("headings", [])),
                        chunk["text"],
                    ]
                )
                chunks.append(chunk)
        return chunks

    def _build_retrieval_index(self) -> None:
        corpus = [chunk["search_text"] for chunk in self.chunks]
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=50000)
        self.chunk_matrix = self.vectorizer.fit_transform(corpus)
        self.chapter_numbers = np.array([chunk["chapter_number"] for chunk in self.chunks])

    def _build_curriculum(self) -> list[Lesson]:
        lessons: list[Lesson] = []
        order_index = 0
        chapters_dir = self.assets_root / "chapters"

        for chapter in self.chapter_index:
            chapter_number = chapter["chapter_number"]
            chapter_title = chapter["chapter_title"]
            chapter_text = chapters_dir.joinpath(chapter["file_name"]).read_text(encoding="utf-8")
            chapter_body = self._strip_front_matter(chapter_text)
            headings = self._collect_meaningful_headings(chapter_body)

            overview_query = " ".join([chapter_title] + headings[:12])
            lessons.append(
                Lesson(
                    lesson_id=f"ch{chapter_number:02d}-overview",
                    chapter_number=chapter_number,
                    chapter_title=chapter_title,
                    order_index=order_index,
                    lesson_kind="overview",
                    title=f"Chapter {chapter_number} Overview",
                    learning_goal=f"Learn the big picture and chapter map for {chapter_title}.",
                    retrieval_query=f"{overview_query} beginner course map overview",
                    start_pdf_page=chapter["start_pdf_page"],
                    end_pdf_page=chapter["end_pdf_page"],
                )
            )
            order_index += 1

            current_page = chapter["start_pdf_page"]
            active_section: str | None = None
            active_objective: str | None = None
            per_chapter_counter: dict[str, int] = {}
            seen_keys: set[tuple[Any, ...]] = set()

            for block in self._split_blocks(chapter_body):
                first_line = block.splitlines()[0].strip()

                if self._is_page_heading(first_line):
                    page = self._extract_pdf_page(first_line)
                    if page is not None:
                        current_page = page
                    continue

                parsed_heading = self._parse_heading(first_line)
                if not parsed_heading:
                    continue
                level, raw_heading = parsed_heading
                heading = self._normalize_heading(raw_heading)
                heading_upper = heading.upper()

                if heading_upper == f"CHAPTER {chapter_number}: {chapter_title}".upper():
                    continue
                if heading_upper.startswith("LO "):
                    active_objective = heading.replace("LO ", "", 1).strip()
                    continue
                if self._should_skip_heading(heading, level, chapter_number):
                    continue

                lesson_kind = self._classify_heading(level, heading_upper)
                if level == 2 and lesson_kind not in {"review", "practice"}:
                    active_section = heading

                display_title = self._display_lesson_title(heading, lesson_kind, active_section)
                dedupe_key = (chapter_number, lesson_kind, display_title.lower(), current_page)
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                counter_key = lesson_kind
                per_chapter_counter[counter_key] = per_chapter_counter.get(counter_key, 0) + 1
                lesson_id = self._lesson_id(
                    chapter_number,
                    lesson_kind,
                    heading,
                    per_chapter_counter[counter_key],
                )

                query_parts = [chapter_title, heading]
                if active_section and active_section != heading:
                    query_parts.append(active_section)
                if active_objective:
                    query_parts.append(active_objective)
                if lesson_kind == "example":
                    query_parts.append("worked example")
                if lesson_kind == "practice":
                    query_parts.append("practice review")

                lessons.append(
                    Lesson(
                        lesson_id=lesson_id,
                        chapter_number=chapter_number,
                        chapter_title=chapter_title,
                        order_index=order_index,
                        lesson_kind=lesson_kind,
                        title=display_title,
                        learning_goal=self._lesson_goal(display_title, lesson_kind, chapter_title, active_section),
                        retrieval_query=" ".join(query_parts) + " beginner explanation",
                        anchor_heading=heading,
                        parent_heading=active_section if active_section and active_section != heading else None,
                        objective_code=active_objective,
                        start_pdf_page=current_page,
                        end_pdf_page=current_page,
                    )
                )
                order_index += 1

        return lessons

    def _collect_meaningful_headings(self, chapter_body: str) -> list[str]:
        headings: list[str] = []
        seen: set[str] = set()
        for block in self._split_blocks(chapter_body):
            first_line = block.splitlines()[0].strip()
            if self._is_page_heading(first_line):
                continue
            parsed = self._parse_heading(first_line)
            if not parsed:
                continue
            level, raw = parsed
            heading = self._normalize_heading(raw)
            if self._should_skip_heading(heading, level, chapter_number=0):
                continue
            if heading not in seen:
                headings.append(heading)
                seen.add(heading)
        return headings

    def _should_skip_heading(self, heading: str, level: int, chapter_number: int) -> bool:
        upper = heading.upper().strip()
        if upper in SKIP_HEADINGS or upper in SKIP_SUBHEADINGS:
            return True
        if upper.startswith("LO ") or upper.startswith("EXHIBIT "):
            return True
        if len(heading) == 1 and heading.isalpha():
            return True
        if upper.startswith("RETURN TO "):
            return True
        if upper.startswith("CODE INDEX"):
            return True
        if upper.startswith("SUBJECT INDEX"):
            return True
        if "ADDITIONAL STUDENT RESOURCES" in upper:
            return True
        if chapter_number and level <= 2 and upper == f"CHAPTER {chapter_number}":
            return True
        return False

    def _strip_front_matter(self, text: str) -> str:
        return FRONT_MATTER_RE.sub("", text, count=1)

    def _split_blocks(self, text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if line.strip():
                current.append(line.rstrip())
                continue
            if current:
                blocks.append("\n".join(current))
                current = []
        if current:
            blocks.append("\n".join(current))
        return blocks

    def _parse_heading(self, line: str) -> tuple[int, str] | None:
        stripped = line.strip()
        pseudo_example = PSEUDO_EXAMPLE_RE.match(stripped)
        if pseudo_example:
            return (3, f"Example {pseudo_example.group(1)}")
        match = HEADING_RE.match(line)
        if not match:
            return None
        return len(match.group(1)), match.group(2).strip()

    def _normalize_heading(self, heading: str) -> str:
        return re.sub(r"\s+", " ", heading.replace("\u00a0", " ")).strip(" -")

    def _is_page_heading(self, line: str) -> bool:
        return bool(PAGE_HEADING_RE.match(line))

    def _extract_pdf_page(self, text: str) -> int | None:
        match = PDF_PAGE_RE.search(text)
        return int(match.group(1)) if match else None

    def _classify_heading(self, level: int, heading_upper: str) -> str:
        if heading_upper in REVIEW_HEADINGS:
            return "review"
        if heading_upper in PRACTICE_HEADINGS:
            return "practice"
        if heading_upper.startswith("EXAMPLE "):
            return "example"
        if level == 2:
            return "section"
        return "concept"

    def _display_lesson_title(self, heading: str, lesson_kind: str, active_section: str | None) -> str:
        if lesson_kind == "example" and active_section and active_section != heading:
            return f"{heading} - {active_section}"
        if lesson_kind == "concept" and active_section and active_section not in {heading, None}:
            if heading.lower() == active_section.lower():
                return f"Key Facts: {heading}"
            if heading.endswith("?") or len(heading.split()) >= 5:
                return heading
            short_parent = active_section if len(active_section) < 60 else active_section[:57] + "..."
            return f"{heading} - {short_parent}"
        return heading

    def _lesson_id(self, chapter_number: int, lesson_kind: str, heading: str, sequence: int) -> str:
        if lesson_kind == "example":
            match = re.search(r"Example\s+(\d+-\d+)", heading, re.IGNORECASE)
            if match:
                return f"ch{chapter_number:02d}-example-{match.group(1).replace('-', '_')}"
        return f"ch{chapter_number:02d}-{lesson_kind}-{sequence:02d}"

    def _lesson_goal(self, title: str, lesson_kind: str, chapter_title: str, active_section: str | None) -> str:
        if lesson_kind == "overview":
            return f"Learn the big picture and chapter map for {chapter_title}."
        if lesson_kind == "section":
            return f"Understand the section '{title}' in Chapter {chapter_title}."
        if lesson_kind == "concept":
            return f"Learn the concept '{title}' and how it fits into {active_section or chapter_title}."
        if lesson_kind == "example":
            return f"Work through {title} and see how the rule is applied."
        if lesson_kind == "review":
            return f"Review the most important takeaways from {chapter_title}."
        if lesson_kind == "practice":
            return f"Practice and check understanding for {chapter_title}."
        return f"Study {title}."

    def _write_response_schemas(self) -> None:
        lesson_schema = {
            "type": "object",
            "properties": {
                "card_type": {"type": "string"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "intro": {"type": "string"},
                "scope_note": {"type": "string"},
                "teaching_points": {"type": "array", "items": {"type": "string"}},
                "worked_example": {"type": "array", "items": {"type": "string"}},
                "flashcards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "front": {"type": "string"},
                            "back": {"type": "string"},
                        },
                        "required": ["front", "back"],
                        "additionalProperties": False,
                    },
                },
                "quiz_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "hint": {"type": "string"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "text": {"type": "string"},
                                        "why": {"type": "string"},
                                    },
                                    "required": ["label", "text", "why"],
                                    "additionalProperties": False,
                                },
                            },
                            "correct_option": {"type": "string"},
                            "study_answer": {"type": "string"},
                        },
                        "required": ["prompt", "hint", "options", "correct_option", "study_answer"],
                        "additionalProperties": False,
                    },
                },
                "memory_trick": {"type": "string"},
                "next_step": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "chunk_id": {"type": "string"},
                            "chapter_number": {"type": "integer"},
                            "pages": {"type": "string"},
                            "why_this_chunk": {"type": "string"},
                        },
                        "required": ["chunk_id", "chapter_number", "pages", "why_this_chunk"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "card_type",
                "title",
                "subtitle",
                "intro",
                "scope_note",
                "teaching_points",
                "worked_example",
                "flashcards",
                "quiz_questions",
                "memory_trick",
                "next_step",
                "citations",
            ],
            "additionalProperties": False,
        }
        grade_schema = {
            "type": "object",
            "properties": {
                "overall_summary": {"type": "string"},
                "question_feedback": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_number": {"type": "integer"},
                            "verdict": {"type": "string"},
                            "explanation": {"type": "string"},
                            "ideal_answer": {"type": "string"},
                        },
                        "required": ["question_number", "verdict", "explanation", "ideal_answer"],
                        "additionalProperties": False,
                    },
                },
                "next_step": {"type": "string"},
            },
            "required": ["overall_summary", "question_feedback", "next_step"],
            "additionalProperties": False,
        }
        self.lesson_schema_path.write_text(json.dumps(lesson_schema, indent=2) + "\n", encoding="utf-8")
        self.grade_schema_path.write_text(json.dumps(grade_schema, indent=2) + "\n", encoding="utf-8")

    def bootstrap(self, client_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            state = self._load_state(client_id=client_id)
            payload = self._compose_state(
                state,
                include_course=False,
                include_plan=False,
                include_last_card=False,
            )
        self._start_initial_warm(state)
        return payload

    def course_payload(self, client_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            state = self._load_state(client_id=client_id)
            payload = self._compose_state(
                state,
                include_course=True,
                include_plan=False,
                include_last_card=False,
            )
        return {
            "chapters": payload["chapters"],
            "chapter_count": payload["chapter_count"],
            "lesson_count": payload["lesson_count"],
            "updated_at": payload["updated_at"],
        }

    def plan_payload(self, client_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            state = self._load_state(client_id=client_id)
            active_lessons = self._active_lessons(state)
            weekly_plan = self._build_weekly_plan(state, active_lessons)
            current_index = self._current_week_index(weekly_plan)
            preview = self._weekly_plan_preview(weekly_plan, window=12)
        return {
            "weekly_goal_lessons": state["weekly_goal_lessons"],
            "midterm_mode": state["midterm_mode"],
            "weekly_plan": weekly_plan,
            "weekly_plan_preview": preview,
            "weekly_plan_current_index": current_index,
            "weekly_plan_total_count": len(weekly_plan),
            "updated_at": state.get("updated_at"),
        }

    def handle_action(self, action: str, payload: dict[str, Any] | None = None, client_id: str | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self.lock:
            state = self._load_state(client_id=client_id)
            card: dict[str, Any] | None = None
            clear_last_card = False

            if action == "next_lesson":
                lesson = self._resolve_next_lesson(state)
                state["current_lesson_id"] = lesson.lesson_id
                card = self._build_lesson_card(lesson, mode="lesson", state=state)
            elif action == "open_lesson":
                lesson_id = str(payload.get("lesson_id", "")).strip()
                lesson = self.lesson_lookup[lesson_id]
                state["current_lesson_id"] = lesson.lesson_id
                card = self._build_lesson_card(lesson, mode="lesson", state=state)
            elif action == "complete_and_continue":
                current = self._require_current_lesson(state)
                recall_text = str(payload.get("recall_text", "")).strip()
                if len(recall_text.split()) < 4:
                    raise ValueError("Before continuing, write one sentence (at least 4 words) that explains the core rule.")
                if current.lesson_id not in state["completed_lessons"]:
                    state["completed_lessons"].append(current.lesson_id)
                self._mark_lesson_completed(state, current.lesson_id)
                perf = state["lesson_performance"].setdefault(current.lesson_id, {})
                perf["last_recall_text"] = recall_text
                perf["last_recall_at"] = self._now()
                remaining_lessons = [lesson for lesson in self._active_lessons(state) if lesson.lesson_id not in state["completed_lessons"]]
                if remaining_lessons:
                    next_lesson = self._resolve_next_lesson(state, after_lesson_id=current.lesson_id)
                    state["current_lesson_id"] = next_lesson.lesson_id
                    card = self._build_lesson_card(next_lesson, mode="lesson", state=state)
                else:
                    state["current_lesson_id"] = None
                    clear_last_card = True
            elif action == "explain_simpler":
                lesson = self._require_current_lesson(state)
                card = self._build_lesson_card(lesson, mode="simpler", state=state)
            elif action == "another_example":
                lesson = self._require_current_lesson(state)
                card = self._build_lesson_card(lesson, mode="example", state=state)
            elif action == "quiz_me":
                lesson = self._require_current_lesson(state)
                card = self._build_lesson_card(lesson, mode="quiz", state=state)
            elif action == "preview_lesson":
                lesson_id = str(payload.get("lesson_id", "")).strip()
                lesson = self.lesson_lookup[lesson_id]
                preview_state = copy.deepcopy(state)
                card = self._build_lesson_card(lesson, mode="lesson", state=preview_state)
                preview_payload = self._compose_state(state)
                preview_current = asdict(lesson)
                position, total = self.lesson_positions.get(lesson.lesson_id, (1, 1))
                preview_current["position_in_chapter"] = position
                preview_current["chapter_lesson_total"] = total
                preview_payload["current_lesson"] = preview_current
                preview_payload["last_card"] = card
                return {"state": preview_payload, "card": card, "preview": True}
            elif action == "review_session":
                card = self._build_review_card(state)
            elif action == "build_exam":
                exam_mode = str(payload.get("exam_mode", "")).strip()
                card = self._build_exam_card(state, exam_mode)
            elif action == "build_diagnostic":
                card = self._build_exam_card(state, "diagnostic_pretest")
            elif action == "build_workpaper":
                card = self._build_exam_card(state, "workpaper_drill")
            elif action == "submit_quiz":
                lesson = self._require_current_lesson(state)
                answers = payload.get("answers") or []
                card = self._grade_quiz_for_current_card(state, lesson, answers)
                card["cumulative_mini_quiz"] = self._build_cumulative_mini_quiz(state, lesson.lesson_id)
                # Auto-save milestone: grading a quiz marks the current lesson complete.
                if lesson.lesson_id not in state["completed_lessons"]:
                    state["completed_lessons"].append(lesson.lesson_id)
                self._mark_lesson_completed(state, lesson.lesson_id)
            elif action == "ask_question":
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("Please enter a question first.")
                current = self.lesson_lookup.get(state.get("current_lesson_id", ""))
                card = self._build_answer_card(question, current, state)
            elif action == "teach_back":
                response_text = str(payload.get("response_text", "")).strip()
                if not response_text:
                    raise ValueError("Please enter your teach-back explanation first.")
                card = self._build_teach_back_card(state, response_text)
            elif action == "rate_flashcard":
                card_id = str(payload.get("card_id", "")).strip()
                rating = str(payload.get("rating", "")).strip().lower()
                self._rate_flashcard(state, card_id, rating)
            elif action == "set_midterm_mode":
                start_chapter = int(payload.get("start_chapter", 1))
                end_chapter = int(payload.get("end_chapter", self.max_chapter_number))
                enabled = bool(payload.get("enabled", False))
                self._set_midterm_mode(state, enabled, start_chapter, end_chapter)
            elif action == "set_weekly_goal":
                weekly_goal = int(payload.get("weekly_goal_lessons", 2))
                state["weekly_goal_lessons"] = max(1, min(5, weekly_goal))
            elif action == "start_over":
                state = self._default_state()
            elif action == "hydrate_state":
                incoming = payload.get("state")
                if not isinstance(incoming, dict):
                    raise ValueError("Hydration requires a valid state object.")
                state = self._normalize_state(incoming)
            else:
                raise ValueError(f"Unsupported action: {action}")

            if clear_last_card:
                state["last_card"] = None
            elif card is not None:
                state["last_card"] = card
            state["updated_at"] = self._now()
            self._save_state(state, client_id=client_id)
            return {"state": self._compose_state(state), "card": card}

    def _default_state(self) -> dict[str, Any]:
        return {
            "current_lesson_id": self.lesson_ids[0] if self.lesson_ids else None,
            "completed_lessons": [],
            "last_card": None,
            "updated_at": self._now(),
            "flashcards": {},
            "lesson_performance": {},
            "mistake_notebook": [],
            "weekly_goal_lessons": 2,
            "midterm_mode": {
                "enabled": False,
                "start_chapter": 1,
                "end_chapter": self.max_chapter_number,
            },
        }

    def _state_path_for_client(self, client_id: str | None = None) -> Path:
        if not client_id:
            return self.state_path
        safe = "".join(ch for ch in str(client_id).lower() if ch.isalnum() or ch in {"-", "_"})
        safe = safe[:64].strip("-_")
        if not safe:
            return self.state_path
        return self.data_root / f"study_state_{safe}.json"

    def _load_state(self, client_id: str | None = None) -> dict[str, Any]:
        path = self._state_path_for_client(client_id)
        if not path.exists():
            return self._default_state()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return self._normalize_state(loaded)

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = self._default_state()
        if "current_lesson_id" in state:
            normalized["current_lesson_id"] = state.get("current_lesson_id")
        normalized["completed_lessons"] = [lesson_id for lesson_id in state.get("completed_lessons", []) if lesson_id in self.lesson_lookup]
        last_card = state.get("last_card")
        has_modern_quiz = isinstance(last_card, dict) and all(
            "study_answer" in question and "options" in question and "correct_option" in question
            for question in last_card.get("quiz_questions", [])
        )
        normalized["last_card"] = self._normalize_last_card(last_card, has_modern_quiz)
        normalized["updated_at"] = state.get("updated_at") or normalized["updated_at"]
        flashcards = state.get("flashcards", {})
        if isinstance(flashcards, dict):
            normalized["flashcards"] = {
                card_id: flashcard
                for card_id, flashcard in flashcards.items()
                if isinstance(flashcard, dict) and flashcard.get("lesson_id") in self.lesson_lookup
            }
        else:
            normalized["flashcards"] = {}
        lesson_performance = state.get("lesson_performance", {})
        normalized["lesson_performance"] = lesson_performance if isinstance(lesson_performance, dict) else {}
        mistake_notebook = state.get("mistake_notebook", [])
        if isinstance(mistake_notebook, list):
            normalized_notebook = []
            current_lesson_id = normalized["current_lesson_id"]
            for entry in mistake_notebook:
                if not isinstance(entry, dict):
                    continue
                migrated = dict(entry)
                reopen_lesson_id = migrated.get("reopen_lesson_id")
                if reopen_lesson_id not in self.lesson_lookup:
                    if migrated.get("lesson_id") in self.lesson_lookup:
                        reopen_lesson_id = migrated["lesson_id"]
                    elif current_lesson_id in self.lesson_lookup:
                        reopen_lesson_id = current_lesson_id
                    else:
                        reopen_lesson_id = None
                migrated["reopen_lesson_id"] = reopen_lesson_id
                normalized_notebook.append(migrated)
            normalized["mistake_notebook"] = normalized_notebook
        else:
            normalized["mistake_notebook"] = []
        normalized["weekly_goal_lessons"] = max(1, min(5, int(state.get("weekly_goal_lessons", 2))))
        midterm = state.get("midterm_mode", {})
        normalized["midterm_mode"] = {
            "enabled": bool(midterm.get("enabled", False)),
            "start_chapter": max(1, min(self.max_chapter_number, int(midterm.get("start_chapter", 1)))),
            "end_chapter": max(1, min(self.max_chapter_number, int(midterm.get("end_chapter", self.max_chapter_number)))),
        }
        if normalized["midterm_mode"]["start_chapter"] > normalized["midterm_mode"]["end_chapter"]:
            normalized["midterm_mode"]["start_chapter"], normalized["midterm_mode"]["end_chapter"] = (
                normalized["midterm_mode"]["end_chapter"],
                normalized["midterm_mode"]["start_chapter"],
            )
        if normalized["current_lesson_id"] is not None and normalized["current_lesson_id"] not in self.lesson_lookup:
            normalized["current_lesson_id"] = self.lesson_ids[0] if self.lesson_ids else None
        return normalized

    def _normalize_last_card(self, last_card: Any, has_modern_quiz: bool) -> dict[str, Any] | None:
        if not (
            isinstance(last_card, dict)
            and "coverage" in last_card
            and "quiz_questions" in last_card
            and "flashcards" in last_card
            and has_modern_quiz
        ):
            return None
        lesson_id = str(last_card.get("lesson_id", "")).strip()
        card_type = str(last_card.get("card_type", "")).strip().lower()
        if lesson_id and lesson_id not in self.lesson_lookup and card_type not in {"review", "exam", "question"}:
            return None
        normalized_card = dict(last_card)
        if lesson_id in self.lesson_lookup:
            position, total = self.lesson_positions.get(lesson_id, (1, 1))
            normalized_card["coverage"] = {
                "position_in_chapter": position,
                "chapter_lesson_total": total,
            }
            normalized_card["reopen_lesson_id"] = lesson_id
            lesson = self.lesson_lookup[lesson_id]
            normalized_card["chapter_number"] = lesson.chapter_number
            normalized_card["chapter_title"] = lesson.chapter_title
        return normalized_card

    def _save_state(self, state: dict[str, Any], client_id: str | None = None) -> None:
        path = self._state_path_for_client(client_id)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _now_dt(self) -> datetime:
        return datetime.now(timezone.utc)

    def _lesson_in_scope(self, lesson: Lesson, state: dict[str, Any]) -> bool:
        midterm = state["midterm_mode"]
        if not midterm["enabled"]:
            return True
        return midterm["start_chapter"] <= lesson.chapter_number <= midterm["end_chapter"]

    def _active_lessons(self, state: dict[str, Any]) -> list[Lesson]:
        active = [lesson for lesson in self.lessons if self._lesson_in_scope(lesson, state)]
        return active or self.lessons

    def _resolve_next_lesson(self, state: dict[str, Any], after_lesson_id: str | None = None) -> Lesson:
        active_lesson_ids = [lesson.lesson_id for lesson in self._active_lessons(state)]
        completed = set(state["completed_lessons"])
        if after_lesson_id and after_lesson_id in active_lesson_ids:
            start_index = active_lesson_ids.index(after_lesson_id) + 1
            candidate_ids = active_lesson_ids[start_index:] + active_lesson_ids[:start_index]
        else:
            current_id = state.get("current_lesson_id")
            if current_id in active_lesson_ids and current_id not in completed:
                return self.lesson_lookup[current_id]
            candidate_ids = active_lesson_ids

        for lesson_id in candidate_ids:
            if lesson_id not in completed:
                return self.lesson_lookup[lesson_id]
        return self.lesson_lookup[active_lesson_ids[-1]]

    def _require_current_lesson(self, state: dict[str, Any]) -> Lesson:
        lesson_id = state.get("current_lesson_id")
        if lesson_id and lesson_id in self.lesson_lookup:
            lesson = self.lesson_lookup[lesson_id]
            if self._lesson_in_scope(lesson, state):
                return lesson
        lesson = self._resolve_next_lesson(state)
        state["current_lesson_id"] = lesson.lesson_id
        return lesson

    def _compose_state(
        self,
        state: dict[str, Any],
        *,
        include_course: bool = True,
        include_plan: bool = True,
        include_last_card: bool = True,
    ) -> dict[str, Any]:
        completed = set(state["completed_lessons"])
        current_id = state.get("current_lesson_id")
        active_lessons = self._active_lessons(state)
        active_lesson_ids = {lesson.lesson_id for lesson in active_lessons}
        weekly_plan = self._build_weekly_plan(state, active_lessons)
        current_week_index = self._current_week_index(weekly_plan)
        weekly_plan_preview = self._weekly_plan_preview(weekly_plan, window=12)
        next_flashcard = self._next_due_flashcard(state)
        current_lesson = self.lesson_lookup.get(current_id) if current_id else None

        chapters: list[dict[str, Any]] = []
        for chapter in self.chapter_index:
            chapter_lessons = self.chapter_lessons[chapter["chapter_number"]]
            chapter_lessons_in_scope = [lesson for lesson in chapter_lessons if lesson.lesson_id in active_lesson_ids]
            lessons_payload = []
            completed_count = 0
            for lesson in chapter_lessons:
                is_completed = lesson.lesson_id in completed
                if is_completed and lesson.lesson_id in active_lesson_ids:
                    completed_count += 1
                if include_course:
                    position, total = self.lesson_positions[lesson.lesson_id]
                    lessons_payload.append(
                        {
                            "lesson_id": lesson.lesson_id,
                            "title": lesson.title,
                            "lesson_kind": lesson.lesson_kind,
                            "objective_code": lesson.objective_code,
                            "learning_goal": lesson.learning_goal,
                            "completed": is_completed,
                            "current": lesson.lesson_id == current_id,
                            "in_scope": lesson.lesson_id in active_lesson_ids,
                            "position_in_chapter": position,
                            "chapter_lesson_total": total,
                        }
                    )
            chapters.append(
                {
                    "chapter_number": chapter["chapter_number"],
                    "chapter_title": chapter["chapter_title"],
                    "start_pdf_page": chapter["start_pdf_page"],
                    "end_pdf_page": chapter["end_pdf_page"],
                    "lesson_count": len(chapter_lessons_in_scope),
                    "completed_lesson_count": completed_count,
                    "in_scope": any(lesson.lesson_id in active_lesson_ids for lesson in chapter_lessons),
                    "lessons": lessons_payload,
                }
            )

        current_payload = asdict(current_lesson) if current_lesson else None
        if current_payload and current_lesson:
            position, total = self.lesson_positions[current_lesson.lesson_id]
            current_payload["position_in_chapter"] = position
            current_payload["chapter_lesson_total"] = total
            current_payload["completion_quality"] = self._lesson_integrity_badge(state, current_lesson.lesson_id)
        upcoming_lessons = self._build_up_next(state, limit=5)
        today_assignment = self._build_today_assignment(state, current_lesson, upcoming_lessons)
        chapter_mastery = self._build_chapter_mastery(state, current_lesson)
        learning_objective_mastery = self._build_learning_objective_mastery(state)
        exam_center = self._build_exam_center(state, current_lesson)
        mistake_notebook = self._build_mistake_notebook(state)

        return {
            "book_title": "Taxation of Individuals and Business Entities 2025",
            "chapter_count": len(self.chapter_index),
            "lesson_count": len(active_lessons),
            "completed_lesson_count": len([lesson_id for lesson_id in completed if lesson_id in active_lesson_ids]),
            "completed_lessons": [lesson_id for lesson_id in state["completed_lessons"] if lesson_id in active_lesson_ids],
            "current_lesson": current_payload,
            "has_saved_card": bool(state.get("last_card")),
            "last_card": state.get("last_card") if include_last_card else None,
            "updated_at": state.get("updated_at"),
            "chapters": chapters,
            "weekly_goal_lessons": state["weekly_goal_lessons"],
            "weekly_plan": weekly_plan if include_plan else weekly_plan_preview,
            "weekly_plan_is_preview": not include_plan,
            "weekly_plan_total_count": len(weekly_plan),
            "weekly_plan_current_index": current_week_index,
            "up_next": upcoming_lessons,
            "today_assignment": today_assignment,
            "chapter_mastery": chapter_mastery,
            "learning_objective_mastery": learning_objective_mastery,
            "exam_center": exam_center,
            "mistake_notebook": mistake_notebook,
            "midterm_mode": state["midterm_mode"],
            "flashcard_total_count": len(state["flashcards"]),
            "flashcards_due_count": len(self._due_flashcards(state)),
            "next_due_flashcard": next_flashcard,
            "lesson_integrity_counts": self._lesson_integrity_counts(state),
        }

    def _lesson_integrity_badge(self, state: dict[str, Any], lesson_id: str) -> dict[str, str]:
        perf = state["lesson_performance"].get(lesson_id, {})
        attempts = int(perf.get("attempts", 0) or 0)
        best_correct = int(perf.get("best_correct_count", 0) or 0)
        completed = lesson_id in state["completed_lessons"]
        if completed and attempts > 0 and best_correct >= 2:
            return {"status": "with_pass", "label": "Completed with quiz passed"}
        if completed:
            return {"status": "without_pass", "label": "Completed without pass"}
        return {"status": "in_progress", "label": "In progress"}

    def _lesson_integrity_counts(self, state: dict[str, Any]) -> dict[str, int]:
        counts = {"with_pass": 0, "without_pass": 0}
        for lesson_id in state["completed_lessons"]:
            status = self._lesson_integrity_badge(state, lesson_id).get("status")
            if status in counts:
                counts[status] += 1
        return counts

    def _build_learning_objective_mastery(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        objective_map: dict[str, dict[str, Any]] = {}
        unresolved_mistakes = self._unresolved_mistakes(state)
        unresolved_by_lesson: dict[str, int] = {}
        for entry in unresolved_mistakes:
            lesson_id = entry.get("reopen_lesson_id") or entry.get("lesson_id")
            if lesson_id:
                unresolved_by_lesson[lesson_id] = unresolved_by_lesson.get(lesson_id, 0) + 1

        for lesson in self._active_lessons(state):
            objective_code = (lesson.objective_code or "").strip()
            if not objective_code:
                continue
            bucket = objective_map.setdefault(
                objective_code,
                {
                    "objective_code": objective_code,
                    "lesson_count": 0,
                    "completed_count": 0,
                    "attempts": 0,
                    "best_correct_points": 0,
                    "possible_correct_points": 0,
                    "unresolved_mistakes": 0,
                },
            )
            bucket["lesson_count"] += 1
            if lesson.lesson_id in state["completed_lessons"]:
                bucket["completed_count"] += 1

            perf = state["lesson_performance"].get(lesson.lesson_id, {})
            attempts = int(perf.get("attempts", 0) or 0)
            best_correct = int(perf.get("best_correct_count", 0) or 0)
            bucket["attempts"] += attempts
            bucket["best_correct_points"] += max(0, min(3, best_correct))
            bucket["possible_correct_points"] += 3
            bucket["unresolved_mistakes"] += unresolved_by_lesson.get(lesson.lesson_id, 0)

        objectives = list(objective_map.values())
        for objective in objectives:
            completion_ratio = objective["completed_count"] / max(1, objective["lesson_count"])
            correctness_ratio = objective["best_correct_points"] / max(1, objective["possible_correct_points"])
            score = round((completion_ratio * 0.55 + correctness_ratio * 0.45) * 100)
            if objective["unresolved_mistakes"] > 0:
                score = max(0, score - min(25, objective["unresolved_mistakes"] * 4))

            if score >= 85:
                status = "strong"
            elif score >= 60:
                status = "developing"
            else:
                status = "needs_focus"
            objective["mastery_score"] = score
            objective["status"] = status

        objectives.sort(key=lambda item: (item["mastery_score"], item["objective_code"]))
        return objectives

    def _current_week_index(self, weekly_plan: list[dict[str, Any]]) -> int:
        for index, week in enumerate(weekly_plan):
            if week["completed_lesson_count"] < week["lesson_count"]:
                return index
        return max(0, len(weekly_plan) - 1)

    def _weekly_plan_preview(self, weekly_plan: list[dict[str, Any]], window: int = 12) -> list[dict[str, Any]]:
        if not weekly_plan:
            return []
        current_index = self._current_week_index(weekly_plan)
        start = max(0, current_index - 1)
        end = min(len(weekly_plan), start + max(1, window))
        return weekly_plan[start:end]

    def _build_up_next(self, state: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        active_lessons = self._active_lessons(state)
        active_ids = [lesson.lesson_id for lesson in active_lessons]
        completed = set(state["completed_lessons"])
        if not active_ids:
            return []
        current_id = state.get("current_lesson_id")
        start_index = active_ids.index(current_id) if current_id in active_ids else 0
        ordered_ids = active_ids[start_index:] + active_ids[:start_index]
        upcoming: list[dict[str, Any]] = []
        for lesson_id in ordered_ids:
            if lesson_id in completed and lesson_id != current_id:
                continue
            lesson = self.lesson_lookup[lesson_id]
            position, total = self.lesson_positions[lesson.lesson_id]
            upcoming.append(
                {
                    "lesson_id": lesson.lesson_id,
                    "title": lesson.title,
                    "lesson_kind": lesson.lesson_kind,
                    "chapter_number": lesson.chapter_number,
                    "chapter_title": lesson.chapter_title,
                    "position_in_chapter": position,
                    "chapter_lesson_total": total,
                    "current": lesson.lesson_id == current_id,
                }
            )
            if len(upcoming) >= limit:
                break
        return upcoming

    def _build_weekly_plan(self, state: dict[str, Any], active_lessons: list[Lesson]) -> list[dict[str, Any]]:
        completed = set(state["completed_lessons"])
        lessons_per_week = state["weekly_goal_lessons"]
        plan: list[dict[str, Any]] = []
        for start in range(0, len(active_lessons), lessons_per_week):
            group = active_lessons[start : start + lessons_per_week]
            chapter_numbers = {lesson.chapter_number for lesson in group}
            week_number = len(plan) + 1
            completed_count = sum(1 for lesson in group if lesson.lesson_id in completed)
            chapter_number_list = sorted(chapter_numbers)
            chapter_span = (
                str(chapter_number_list[0])
                if len(chapter_number_list) == 1
                else f"{chapter_number_list[0]}-{chapter_number_list[-1]}"
            )
            focus_titles: list[str] = []
            for lesson in group:
                if lesson.title not in focus_titles:
                    focus_titles.append(lesson.title)
            plan.append(
                {
                    "week_number": week_number,
                    "lesson_count": len(group),
                    "completed_lesson_count": completed_count,
                    "chapter_span": chapter_span,
                    "focus_titles": focus_titles[:3],
                    "lesson_ids": [lesson.lesson_id for lesson in group],
                }
            )
        return plan

    def _build_today_assignment(
        self,
        state: dict[str, Any],
        current_lesson: Lesson | None,
        upcoming_lessons: list[dict[str, Any]],
    ) -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        due_flashcards = self._due_flashcards(state)
        unresolved_mistakes = self._unresolved_mistakes(state)
        performance = state["lesson_performance"].get(current_lesson.lesson_id, {}) if current_lesson else {}
        quiz_done = bool(int(performance.get("attempts", 0) or 0) > 0)

        if current_lesson:
            position, total = self.lesson_positions.get(current_lesson.lesson_id, (1, 1))
            tasks.append(
                {
                    "task_id": "current_lesson",
                    "title": "Finish your current lesson",
                    "detail": f"Chapter {current_lesson.chapter_number} · Lesson {position}/{total} · {current_lesson.title}",
                    "cta_label": "Resume lesson",
                    "action": "open_lesson",
                    "payload": {"lesson_id": current_lesson.lesson_id},
                    "kind": "lesson",
                }
            )
            if not quiz_done:
                tasks.append(
                    {
                        "task_id": "lesson_quiz",
                        "title": "Take the lesson quiz",
                        "detail": "Finish the quick check so the app can measure how strong this lesson is.",
                        "cta_label": "Open quiz",
                        "action": "quiz_me",
                        "payload": {},
                        "kind": "quiz",
                    }
                )

        if due_flashcards:
            tasks.append(
                {
                    "task_id": "flashcards",
                    "title": "Review due flashcards",
                    "detail": f"{len(due_flashcards)} flashcards are ready. This is the fastest retention win today.",
                    "cta_label": "Go to flashcards",
                    "ui_action": "flashcards",
                    "kind": "flashcards",
                }
            )

        if unresolved_mistakes:
            tasks.append(
                {
                    "task_id": "mistakes",
                    "title": "Clean up your mistake notebook",
                    "detail": f"{len(unresolved_mistakes)} missed questions still need one more look.",
                    "cta_label": "Go to notebook",
                    "ui_action": "mistake_notebook",
                    "kind": "mistake",
                }
            )

        if len(tasks) < 3 and upcoming_lessons:
            next_real = next((item for item in upcoming_lessons if not item.get("current")), None)
            if next_real:
                tasks.append(
                    {
                        "task_id": "preview_next",
                        "title": "See the next lesson on the course map",
                        "detail": f"Look ahead to {next_real['title']} without changing your saved place.",
                        "cta_label": "Open course map",
                        "ui_action": "course_map",
                        "kind": "preview",
                    }
                )

        tasks = tasks[:3]
        estimated_minutes = 12 + (5 * len(tasks))
        headline = "Your daily study path is ready." if tasks else "Start the next lesson to generate your daily path."
        return {
            "headline": headline,
            "estimated_minutes": estimated_minutes,
            "tasks": tasks,
        }

    def _lesson_mastery_status(self, lesson: Lesson, state: dict[str, Any], current_lesson: Lesson | None) -> tuple[str, str]:
        performance = state["lesson_performance"].get(lesson.lesson_id, {})
        unresolved_count = len(
            [
                entry
                for entry in self._unresolved_mistakes(state)
                if (entry.get("reopen_lesson_id") or entry.get("lesson_id")) == lesson.lesson_id
            ]
        )
        best_correct = int(performance.get("best_correct_count", 0) or 0)
        attempts = int(performance.get("attempts", 0) or 0)
        if current_lesson and lesson.lesson_id == current_lesson.lesson_id:
            return ("current", "In progress")
        if unresolved_count > 0:
            return ("needs_review", "Needs review")
        if lesson.lesson_id in state["completed_lessons"] and best_correct >= 3:
            return ("mastered", "Mastered")
        if lesson.lesson_id in state["completed_lessons"]:
            return ("learned", "Learned")
        if attempts > 0:
            return ("started", "Started")
        return ("not_started", "Not started")

    def _build_chapter_mastery(self, state: dict[str, Any], current_lesson: Lesson | None) -> dict[str, Any] | None:
        if not current_lesson:
            return None
        chapter_lessons = [lesson for lesson in self.chapter_lessons[current_lesson.chapter_number] if self._lesson_in_scope(lesson, state)]
        if not chapter_lessons:
            return None
        tiles: list[dict[str, Any]] = []
        status_points = {
            "not_started": 0,
            "started": 35,
            "current": 50,
            "learned": 75,
            "needs_review": 45,
            "mastered": 100,
        }
        total_points = 0
        for lesson in chapter_lessons:
            status, label = self._lesson_mastery_status(lesson, state, current_lesson)
            position, total = self.lesson_positions.get(lesson.lesson_id, (1, 1))
            performance = state["lesson_performance"].get(lesson.lesson_id, {})
            best_correct = int(performance.get("best_correct_count", 0) or 0)
            total_points += status_points.get(status, 0)
            tiles.append(
                {
                    "lesson_id": lesson.lesson_id,
                    "title": lesson.title,
                    "status": status,
                    "status_label": label,
                    "lesson_kind": lesson.lesson_kind,
                    "position_in_chapter": position,
                    "chapter_lesson_total": total,
                    "score_label": f"{best_correct}/3 best" if best_correct else "No quiz yet",
                }
            )
        mastery_percent = round(total_points / max(len(chapter_lessons), 1))
        return {
            "chapter_number": current_lesson.chapter_number,
            "chapter_title": current_lesson.chapter_title,
            "mastery_percent": mastery_percent,
            "tiles": tiles,
        }

    def _build_exam_center(self, state: dict[str, Any], current_lesson: Lesson | None) -> dict[str, Any]:
        current_chapter = current_lesson.chapter_number if current_lesson else 1
        unresolved_count = len(self._unresolved_mistakes(state))
        return {
            "current_chapter_number": current_chapter,
            "modes": [
                {
                    "exam_mode": "chapter_mini",
                    "title": "Chapter Mini-Exam",
                    "detail": f"Pull a mixed check from Chapter {current_chapter}.",
                    "cta_label": "Start mini-exam",
                    "disabled": False,
                },
                {
                    "exam_mode": "mixed_review",
                    "title": "Mixed Chapter Review",
                    "detail": "Blend the current chapter with nearby concepts so you can compare ideas.",
                    "cta_label": "Start mixed review",
                    "disabled": False,
                },
                {
                    "exam_mode": "diagnostic_pretest",
                    "title": "Chapter Diagnostic",
                    "detail": f"Baseline check before deep study in Chapter {current_chapter}.",
                    "cta_label": "Start diagnostic",
                    "disabled": False,
                },
                {
                    "exam_mode": "missed_only",
                    "title": "Missed Questions Only",
                    "detail": unresolved_count
                    and f"Focus on the {unresolved_count} questions in your mistake notebook."
                    or "Unlock this after you miss a few questions.",
                    "cta_label": "Start misses drill",
                    "disabled": unresolved_count == 0,
                },
                {
                    "exam_mode": "workpaper_drill",
                    "title": "Workpaper Drill",
                    "detail": "Practice structured tax calculations step-by-step.",
                    "cta_label": "Start workpaper drill",
                    "disabled": False,
                },
                {
                    "exam_mode": "timed_drill",
                    "title": "Timed 10-Min Drill",
                    "detail": "Short pressure round to practice exam pacing.",
                    "cta_label": "Start timed drill",
                    "disabled": False,
                },
                {
                    "exam_mode": "cumulative_timed",
                    "title": "Cumulative Timed Set",
                    "detail": "Mixed cumulative set across active chapters under time pressure.",
                    "cta_label": "Start cumulative set",
                    "disabled": False,
                },
            ],
        }

    def _unresolved_mistakes(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        entries = [entry for entry in state.get("mistake_notebook", []) if not entry.get("resolved")]
        entries.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return entries

    def _build_mistake_notebook(self, state: dict[str, Any]) -> dict[str, Any]:
        unresolved = self._unresolved_mistakes(state)
        taxonomy_counts: dict[str, int] = {}
        for entry in unresolved:
            category = str(entry.get("taxonomy", "uncategorized"))
            taxonomy_counts[category] = taxonomy_counts.get(category, 0) + 1
        items = [
            {
                "entry_id": entry["entry_id"],
                "lesson_id": entry["lesson_id"],
                "reopen_lesson_id": entry.get("reopen_lesson_id"),
                "lesson_title": entry["lesson_title"],
                "chapter_number": entry["chapter_number"],
                "prompt": entry["prompt"],
                "selected_option": entry["selected_option"],
                "correct_option": entry["correct_option"],
                "why_selected_wrong": entry["why_selected_wrong"],
                "why_correct_right": entry["why_correct_right"],
                "taxonomy": entry.get("taxonomy", "uncategorized"),
                "created_at": entry["created_at"],
            }
            for entry in unresolved[:8]
        ]
        return {
            "unresolved_count": len(unresolved),
            "taxonomy_counts": taxonomy_counts,
            "items": items,
        }

    def _build_lesson_card(self, lesson: Lesson, mode: str, state: dict[str, Any]) -> dict[str, Any]:
        top_k = 5 if lesson.lesson_kind == "overview" else 4
        search_results = self.search_chunks(
            lesson.retrieval_query,
            chapter_numbers={lesson.chapter_number},
            top_k=top_k,
        )
        prompt = self._lesson_prompt(lesson, mode, search_results)
        model_response = self._run_structured_model(
            prompt,
            self.lesson_schema_path,
            cache_key=self._cache_key(mode, lesson.lesson_id, lesson.retrieval_query),
        )
        finalized = self._finalize_card(model_response, lesson, search_results)
        self._merge_flashcards(state, lesson, finalized)
        if mode == "lesson":
            self._warm_related_async(lesson, state)
        return finalized

    def _build_review_card(self, state: dict[str, Any]) -> dict[str, Any]:
        weekly_plan = self._build_weekly_plan(state, self._active_lessons(state))
        completed = set(state["completed_lessons"])
        target_week = None
        for week in weekly_plan:
            if week["completed_lesson_count"] < week["lesson_count"]:
                target_week = week
                break
        if target_week is None and weekly_plan:
            target_week = weekly_plan[-1]
        if target_week is None:
            raise ValueError("No lessons are available to review yet.")

        week_lessons = [self.lesson_lookup[lesson_id] for lesson_id in target_week["lesson_ids"]]
        query = " ".join(lesson.title for lesson in week_lessons[:8]) + " review compare concepts"
        chapter_numbers = {lesson.chapter_number for lesson in week_lessons}
        chunks = self.search_chunks(query, chapter_numbers=chapter_numbers, top_k=6)
        prompt = self._review_prompt(target_week, week_lessons, chunks)
        response = self._run_structured_model(
            prompt,
            self.lesson_schema_path,
            cache_key=self._cache_key("review", f"week-{target_week['week_number']}", query),
        )
        anchor_lesson = next((lesson for lesson in week_lessons if lesson.lesson_id == state.get("current_lesson_id")), week_lessons[0])
        lesson_stub = Lesson(
            lesson_id=f"review-week-{target_week['week_number']}",
            chapter_number=week_lessons[0].chapter_number,
            chapter_title="Weekly Review",
            order_index=-1,
            lesson_kind="review",
            title=f"Week {target_week['week_number']} Review Session",
            learning_goal="Review the week's lessons and strengthen recall.",
            retrieval_query=query,
        )
        finalized = self._finalize_card(response, lesson_stub, chunks)
        finalized["reopen_lesson_id"] = anchor_lesson.lesson_id
        return finalized

    def _build_answer_card(self, question: str, lesson: Lesson | None, state: dict[str, Any]) -> dict[str, Any]:
        chapter_numbers = {lesson.chapter_number} if lesson else None
        search_results = self.search_chunks(question, chapter_numbers=chapter_numbers, top_k=5)
        prompt = self._question_prompt(question, lesson, search_results)
        response = self._run_structured_model(
            prompt,
            self.lesson_schema_path,
            cache_key=self._cache_key("question", lesson.lesson_id if lesson else "none", question),
        )
        lesson_stub = lesson or Lesson(
            lesson_id="free-question",
            chapter_number=search_results[0]["chapter_number"],
            chapter_title=search_results[0]["chapter_title"],
            order_index=-1,
            lesson_kind="question",
            title="Book-grounded answer",
            learning_goal="Answer a student question from the textbook.",
            retrieval_query=question,
        )
        finalized = self._finalize_card(response, lesson_stub, search_results)
        finalized["reopen_lesson_id"] = lesson.lesson_id if lesson else None
        return finalized

    def _build_exam_card(self, state: dict[str, Any], exam_mode: str) -> dict[str, Any]:
        current_lesson = self._require_current_lesson(state)
        if exam_mode == "chapter_mini":
            lessons = [lesson for lesson in self.chapter_lessons[current_lesson.chapter_number] if self._lesson_in_scope(lesson, state)]
            title = f"Chapter {current_lesson.chapter_number} Mini-Exam"
            subtitle = f"Chapter {current_lesson.chapter_number}: {current_lesson.chapter_title}"
            query = f"{current_lesson.chapter_title} mixed review mini exam"
            chapter_numbers = {current_lesson.chapter_number}
            extra_note = "Mix the most important concepts from this chapter."
            timed_minutes = None
        elif exam_mode == "mixed_review":
            chapter_numbers = {
                chapter
                for chapter in range(max(1, current_lesson.chapter_number - 1), min(self.max_chapter_number, current_lesson.chapter_number + 1) + 1)
            }
            lessons = [lesson for lesson in self._active_lessons(state) if lesson.chapter_number in chapter_numbers][:18]
            title = "Mixed Chapter Review"
            subtitle = f"Chapters {min(chapter_numbers)}-{max(chapter_numbers)}"
            query = " ".join(lesson.title for lesson in lessons[:10]) + " compare concepts mixed review exam"
            extra_note = "Compare similar rules and make the student choose carefully."
            timed_minutes = None
        elif exam_mode == "missed_only":
            unresolved = self._unresolved_mistakes(state)
            if not unresolved:
                raise ValueError("Your mistake notebook is empty right now. Miss a question first, then come back here.")
            targeted_entries = unresolved[:3]
            lesson_ids = {
                entry.get("reopen_lesson_id") or entry["lesson_id"]
                for entry in unresolved[:8]
                if (entry.get("reopen_lesson_id") or entry["lesson_id"]) in self.lesson_lookup
            }
            lessons = [self.lesson_lookup[lesson_id] for lesson_id in lesson_ids if lesson_id in self.lesson_lookup]
            chapter_numbers = {lesson.chapter_number for lesson in lessons} or {current_lesson.chapter_number}
            title = "Missed Questions Drill"
            subtitle = "Built from your mistake notebook"
            query = " ".join(entry["prompt"] for entry in unresolved[:5]) + " fix common confusion review"
            extra_note = "Focus on the student's known errors and common confusions."
            timed_minutes = None
        elif exam_mode == "timed_drill":
            lessons = [lesson for lesson in self._active_lessons(state) if abs(lesson.chapter_number - current_lesson.chapter_number) <= 1][:16]
            chapter_numbers = {lesson.chapter_number for lesson in lessons} or {current_lesson.chapter_number}
            title = "Timed 10-Min Drill"
            subtitle = f"Fast mixed practice around Chapter {current_lesson.chapter_number}"
            query = " ".join(lesson.title for lesson in lessons[:8]) + " timed drill exam pacing"
            extra_note = "Make the questions concise and exam-like."
            timed_minutes = 10
            question_target = 5
        elif exam_mode == "diagnostic_pretest":
            lessons = [lesson for lesson in self.chapter_lessons[current_lesson.chapter_number] if self._lesson_in_scope(lesson, state)][:12]
            chapter_numbers = {current_lesson.chapter_number}
            title = f"Chapter {current_lesson.chapter_number} Diagnostic"
            subtitle = "Pretest your baseline before deeper practice"
            query = f"{current_lesson.chapter_title} diagnostic pretest baseline misconceptions"
            extra_note = "Focus on baseline misconceptions and include broad coverage."
            timed_minutes = 8
            question_target = 5
        elif exam_mode == "workpaper_drill":
            lessons = [lesson for lesson in self._active_lessons(state) if lesson.chapter_number == current_lesson.chapter_number][:16]
            chapter_numbers = {current_lesson.chapter_number}
            title = "Workpaper Drill"
            subtitle = f"Structured tax computation practice for Chapter {current_lesson.chapter_number}"
            query = f"{current_lesson.chapter_title} tax computation worksheet basis rate deduction credit"
            extra_note = "Make each question a mini workpaper: identify given data, choose method, compute result."
            timed_minutes = None
            question_target = 5
        elif exam_mode == "cumulative_timed":
            lessons = self._active_lessons(state)[:36]
            chapter_numbers = {lesson.chapter_number for lesson in lessons}
            title = "Cumulative Timed Set"
            subtitle = "Cross-chapter mixed set"
            query = " ".join(lesson.title for lesson in lessons[:14]) + " cumulative mixed timed tax exam"
            extra_note = "Mix chapters broadly. Include conceptual and computational items."
            timed_minutes = 20
            question_target = 5
        else:
            raise ValueError("Choose a valid exam mode first.")
        if "question_target" not in locals():
            question_target = 5

        chunks = self.search_chunks(query, chapter_numbers=chapter_numbers, top_k=6)
        prompt = self._exam_prompt(title, subtitle, lessons, chunks, extra_note, timed_minutes, question_target=question_target)
        response = self._run_structured_model(
            prompt,
            self.lesson_schema_path,
            cache_key=self._cache_key("exam", f"{exam_mode}-{current_lesson.lesson_id}", query),
        )
        lesson_stub = Lesson(
            lesson_id=f"exam-{exam_mode}-{current_lesson.chapter_number}",
            chapter_number=current_lesson.chapter_number,
            chapter_title=current_lesson.chapter_title,
            order_index=-1,
            lesson_kind="review",
            title=title,
            learning_goal=subtitle,
            retrieval_query=query,
        )
        finalized = self._finalize_card(response, lesson_stub, chunks)
        finalized["card_type"] = "exam"
        finalized["title"] = title
        finalized["subtitle"] = subtitle
        finalized["reopen_lesson_id"] = current_lesson.lesson_id
        if exam_mode == "missed_only":
            for question, entry in zip(finalized.get("quiz_questions", []), targeted_entries, strict=False):
                question["source_entry_id"] = entry.get("entry_id")
                question["reopen_lesson_id"] = entry.get("reopen_lesson_id") or entry.get("lesson_id")
        finalized["exam_meta"] = {
            "exam_mode": exam_mode,
            "timed_minutes": timed_minutes,
        }
        return finalized

    def _grade_quiz_for_current_card(self, state: dict[str, Any], lesson: Lesson, answers: list[str]) -> dict[str, Any]:
        _ = lesson
        last_card = state.get("last_card")
        if not last_card or not last_card.get("quiz_questions"):
            raise ValueError("Open a lesson first so there is a quiz to grade.")
        normalized_answers = [str(answer).strip().upper() for answer in answers]
        expected_count = len(last_card["quiz_questions"])
        if len(normalized_answers) != expected_count or not all(normalized_answers):
            raise ValueError(f"Please answer all {expected_count} quiz questions.")
        if any(answer not in QUIZ_OPTION_LABELS for answer in normalized_answers):
            raise ValueError("Please choose one option from A to E for each question.")
        graded_card = dict(last_card)
        graded_card["quiz_feedback"] = self._grade_quiz_locally(last_card, normalized_answers)
        self._record_quiz_results(state, graded_card)
        return graded_card

    def _grade_quiz_locally(self, card: dict[str, Any], answers: list[str]) -> dict[str, Any]:
        question_feedback: list[dict[str, Any]] = []
        correct_count = 0

        for index, (question, answer) in enumerate(zip(card["quiz_questions"], answers), start=1):
            selected = answer.strip().upper()
            correct_option = str(question.get("correct_option", "")).strip().upper()
            study_answer = question.get("study_answer", "").strip()
            selected_text = self._option_text(question, selected)
            correct_text = self._option_text(question, correct_option)
            selected_why = self._option_why(question, selected)
            correct_why = self._option_why(question, correct_option)
            is_correct = selected == correct_option
            verdict = "correct" if is_correct else "incorrect"
            if is_correct:
                correct_count += 1
                explanation = (
                    f"You picked {selected}"
                    f"{f' ({selected_text})' if selected_text else ''}. "
                    f"That matches the best answer because {correct_why.lower() or study_answer.lower()}"
                )
            else:
                explanation = (
                    f"You picked {selected}"
                    f"{f' ({selected_text})' if selected_text else ''}. "
                    f"That option is off because {selected_why.lower() or 'it does not match the rule from the lesson'}. "
                    f"The correct answer is {correct_option}"
                    f"{f' ({correct_text})' if correct_text else ''} because {correct_why.lower() or study_answer.lower()}"
                )
            question_feedback.append(
                {
                    "question_id": question.get("question_id", f"q{index}"),
                    "question_number": index,
                    "verdict": verdict,
                    "selected_option": selected,
                    "correct_option": correct_option,
                    "selected_text": selected_text,
                    "correct_text": correct_text,
                    "why_selected_wrong": "" if is_correct else (selected_why or "It does not fit the rule that the lesson is testing."),
                    "why_correct_right": correct_why or study_answer,
                    "explanation": explanation,
                    "ideal_answer": (
                        f"{correct_option}. {correct_text}" if correct_text else correct_option
                    )
                    + (f" {study_answer}" if study_answer else ""),
                }
            )

        total_questions = len(question_feedback)
        if correct_count == total_questions:
            overall_summary = f"Nice work. You got all {total_questions} questions right."
            next_step = "Mark the lesson complete, then move to the next lesson or review the flashcards once."
        elif correct_count >= max(1, total_questions - 1):
            overall_summary = f"You got {correct_count} out of {total_questions} right. You are close."
            next_step = "Review the missed explanation once, then try the quiz again or move on if the rule now makes sense."
        elif correct_count > 0:
            overall_summary = f"You got {correct_count} out of {total_questions} right. The foundation is forming, but this lesson needs one more pass."
            next_step = "Read the lesson bullets and worked example again, then retry the quiz before moving on."
        else:
            overall_summary = f"You missed all {total_questions} questions, which usually means this lesson needs a slower walkthrough."
            next_step = "Press Explain Simpler, reread the worked example, and then retry the quiz."

        return {
            "overall_summary": overall_summary,
            "correct_count": correct_count,
            "total_questions": total_questions,
            "question_feedback": question_feedback,
            "next_step": next_step,
        }

    def _set_midterm_mode(self, state: dict[str, Any], enabled: bool, start_chapter: int, end_chapter: int) -> None:
        start_chapter = max(1, min(self.max_chapter_number, start_chapter))
        end_chapter = max(1, min(self.max_chapter_number, end_chapter))
        if start_chapter > end_chapter:
            start_chapter, end_chapter = end_chapter, start_chapter
        state["midterm_mode"] = {
            "enabled": enabled,
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
        }
        current = self.lesson_lookup.get(state.get("current_lesson_id", ""))
        if not current or not self._lesson_in_scope(current, state):
            next_lesson = self._resolve_next_lesson(state)
            state["current_lesson_id"] = next_lesson.lesson_id

    def search_chunks(
        self,
        query: str,
        chapter_numbers: set[int] | None = None,
        top_k: int = 4,
    ) -> list[dict[str, Any]]:
        cache_key = self._search_cache_key(query, chapter_numbers, top_k)
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            self.search_cache.move_to_end(cache_key)
            return [
                {
                    **item,
                    "headings": list(item.get("headings", [])),
                }
                for item in cached
            ]
        query_vector = self.vectorizer.transform([query])
        scores = linear_kernel(query_vector, self.chunk_matrix).ravel()
        if chapter_numbers:
            same_scope = np.isin(self.chapter_numbers, list(chapter_numbers))
            scores = scores.copy()
            scores[same_scope] *= 1.45
            scores[~same_scope] *= 0.72
        ranked = np.argsort(scores)[::-1]
        results: list[dict[str, Any]] = []
        for index in ranked:
            if scores[index] <= 0:
                continue
            chunk = self.chunks[int(index)]
            results.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "chapter_number": chunk["chapter_number"],
                    "chapter_title": chunk["chapter_title"],
                    "start_pdf_page": chunk["start_pdf_page"],
                    "end_pdf_page": chunk["end_pdf_page"],
                    "headings": chunk.get("headings", []),
                    "text": chunk["text"],
                }
            )
            if len(results) >= top_k:
                break
        if not results:
            chunk = self.chunks[0]
            results.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "chapter_number": chunk["chapter_number"],
                    "chapter_title": chunk["chapter_title"],
                    "start_pdf_page": chunk["start_pdf_page"],
                    "end_pdf_page": chunk["end_pdf_page"],
                    "headings": chunk.get("headings", []),
                    "text": chunk["text"],
                }
            )
        self.search_cache[cache_key] = [
            {
                **item,
                "headings": list(item.get("headings", [])),
            }
            for item in results
        ]
        if len(self.search_cache) > 2048:
            self.search_cache.popitem(last=False)
        return results

    def _search_cache_key(self, query: str, chapter_numbers: set[int] | None, top_k: int) -> str:
        chapter_key = ",".join(str(value) for value in sorted(chapter_numbers or []))
        return json.dumps({"query": query, "chapters": chapter_key, "top_k": top_k}, sort_keys=True)

    def _lesson_prompt(self, lesson: Lesson, mode: str, chunks: list[dict[str, Any]]) -> str:
        position, total = self.lesson_positions.get(lesson.lesson_id, (1, 1))
        mode_instructions = {
            "lesson": "Teach this lesson clearly from the ground up.",
            "simpler": "Teach the exact same lesson again in even simpler language, assuming the student is confused.",
            "example": "Focus on a fresh worked example that makes the lesson click.",
            "quiz": "Teach briefly, then make the quiz especially strong and diagnostic.",
        }[mode]
        return "\n".join(
            [
                self.system_prompt,
                "",
                "Additional behavior:",
                "- The student is a complete beginner who often does not know what to ask next.",
                "- Teach proactively and do not skip important textbook concepts that match this lesson scope.",
                "- Keep the lesson focused on this lesson, not the entire chapter at once.",
                "- Use only the textbook context below for book-grounded claims.",
                "- Always produce exactly 3 multiple-choice quiz questions.",
                "- Always produce exactly 3 flashcards.",
                "- Each quiz question must have exactly 5 answer options labeled A, B, C, D, and E.",
                "- Every answer option must include a short `why` explanation saying why that option is right or wrong.",
                "- Each quiz question must include one `correct_option` and a short `study_answer` for quick grading and review.",
                "",
                f"Mode: {mode}",
                f"Task: {mode_instructions}",
                f"Lesson title: {lesson.title}",
                f"Lesson goal: {lesson.learning_goal}",
                f"Lesson kind: {lesson.lesson_kind}",
                f"Chapter: {lesson.chapter_number} - {lesson.chapter_title}",
                f"Lesson position in chapter: {position} of {total}",
                f"Objective code if any: {lesson.objective_code or 'None'}",
                "",
                "Output guidance:",
                "- `scope_note` must clearly say this is one lesson within the chapter, not the whole chapter.",
                "- `teaching_points` should have 5 to 8 short bullets.",
                "- `worked_example` should have 4 to 6 short steps.",
                "- `quiz_questions` must contain exactly 3 items.",
                "- Each quiz question should have 5 plausible options and exactly one correct answer.",
                "- Each quiz question should have a brief `study_answer` of one or two sentences.",
                "- `flashcards` must contain exactly 3 items.",
                "- `citations` must only cite chunk ids from the textbook context below.",
                "",
                "Textbook context:",
                self._format_chunk_context(chunks),
            ]
        )

    def _review_prompt(self, week: dict[str, Any], lessons: list[Lesson], chunks: list[dict[str, Any]]) -> str:
        lesson_list = "\n".join(f"- {lesson.title}" for lesson in lessons[:10])
        return "\n".join(
            [
                self.system_prompt,
                "",
                "Create a teaching-first weekly review session.",
                "- The student is a beginner and needs synthesis, not just repetition.",
                "- Compare similar concepts, point out common confusions, and keep the tone encouraging.",
                "- Always produce exactly 3 multiple-choice quiz questions and 3 flashcards.",
                "- Each quiz question must include 5 options, one `correct_option`, a short `study_answer`, and a short `why` explanation for each option.",
                "",
                f"Week number: {week['week_number']}",
                f"Chapters covered this week: {week['chapter_span']}",
                "Lessons in this review set:",
                lesson_list,
                "",
                "Textbook context:",
                self._format_chunk_context(chunks),
            ]
        )

    def _exam_prompt(
        self,
        title: str,
        subtitle: str,
        lessons: list[Lesson],
        chunks: list[dict[str, Any]],
        extra_note: str,
        timed_minutes: int | None,
        question_target: int = 5,
    ) -> str:
        lesson_list = "\n".join(f"- {lesson.title}" for lesson in lessons[:12])
        return "\n".join(
            [
                self.system_prompt,
                "",
                "Create an exam-style practice card.",
                "- The student is still a beginner, but this should feel more like a checkpoint than a lesson.",
                "- Make the quiz the star. The teaching points should be short and tactical.",
                f"- Always produce exactly 3 flashcards and exactly {question_target} multiple-choice questions.",
                "- Each question must have exactly 5 options labeled A, B, C, D, and E.",
                "- Every option must include a short `why` explanation saying why that option is right or wrong.",
                "- Each question must include one `correct_option` and a short `study_answer`.",
                "- Keep the tone encouraging, not harsh.",
                "",
                f"Exam title: {title}",
                f"Exam subtitle: {subtitle}",
                f"Timed minutes: {timed_minutes or 'untimed'}",
                f"Extra note: {extra_note}",
                "Source lessons:",
                lesson_list,
                "",
                "Textbook context:",
                self._format_chunk_context(chunks),
            ]
        )

    def _question_prompt(self, question: str, lesson: Lesson | None, chunks: list[dict[str, Any]]) -> str:
        lesson_hint = ""
        if lesson:
            lesson_hint = f"Current lesson: {lesson.title} in Chapter {lesson.chapter_number}: {lesson.chapter_title}."
        return "\n".join(
            [
                self.system_prompt,
                "",
                "Answer the student's question in a teaching-first way.",
                "- Start with a direct answer in plain English.",
                "- Then teach the idea step by step.",
                "- If the student uses fuzzy language, interpret it kindly and define the proper term.",
                "- Always produce exactly 3 multiple-choice quiz questions and 3 flashcards tied to the answer.",
                "- Each quiz question must include 5 options, one `correct_option`, a short `study_answer`, and a short `why` explanation for each option.",
                "",
                lesson_hint,
                f"Student question: {question}",
                "",
                "Textbook context:",
                self._format_chunk_context(chunks),
            ]
        )

    def _format_chunk_context(self, chunks: list[dict[str, Any]]) -> str:
        pieces: list[str] = []
        remaining = 7600
        for chunk in chunks:
            pages = self._pages_label(chunk["start_pdf_page"], chunk["end_pdf_page"])
            headings = ", ".join(chunk.get("headings") or [])
            block = "\n".join(
                [
                    f"[{chunk['chunk_id']}] Chapter {chunk['chapter_number']}: {chunk['chapter_title']}",
                    f"Pages: {pages}",
                    f"Headings: {headings or 'None listed'}",
                    "Excerpt:",
                    chunk["text"].strip(),
                ]
            )
            if len(block) > remaining and pieces:
                break
            pieces.append(block)
            remaining -= len(block)
        return "\n\n".join(pieces)

    def _run_structured_model(self, prompt: str, schema_path: Path, cache_key: str | None = None) -> dict[str, Any]:
        cache_path = self.cache_root / f"{cache_key}.json" if cache_key else None
        if cache_path and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        provider_error: str | None = None
        api_key = os.environ.get("OPENCODE_API_KEY", "").strip()
        if api_key:
            try:
                response = self._run_openai_compatible_model(prompt, schema_path, api_key=api_key)
                if cache_path:
                    cache_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
                return response
            except Exception as exc:
                # Keep current behavior: degrade gracefully to local/codex fallback.
                provider_error = f"{type(exc).__name__}: {exc}"
                print(f"[tax-tutor] OpenCode provider failed, using fallback. {provider_error}", file=sys.stderr)

        if shutil.which("codex") is None:
            return self._fallback_response(provider_error or "local_mode", schema_path)

        success = False
        try:
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False, encoding="utf-8") as out_file:
                out_path = Path(out_file.name)
            cmd = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                f'model_reasoning_effort="{os.environ.get("TAX_TUTOR_CODEX_REASONING", "low")}"',
                "--model",
                os.environ.get("TAX_TUTOR_CODEX_MODEL", "gpt-5.4-mini"),
                "--ephemeral",
                "--color",
                "never",
                "-C",
                str(self.model_workdir),
                "--output-schema",
                str(schema_path),
                "-o",
                str(out_path),
                "-",
            ]
            completed = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Codex command failed.")
            response = json.loads(out_path.read_text(encoding="utf-8").strip())
            success = True
        except Exception as exc:
            response = self._fallback_response(str(exc), schema_path)
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except UnboundLocalError:
                pass

        if cache_path and success:
            cache_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
        return response

    def _run_openai_compatible_model(self, prompt: str, schema_path: Path, *, api_key: str) -> dict[str, Any]:
        base_url = os.environ.get("OPENCODE_BASE_URL", "https://api.moonshot.ai/v1").strip().rstrip("/")
        model = os.environ.get("OPENCODE_MODEL", "kimi-k2.6").strip() or "kimi-k2.6"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        endpoint = f"{base_url}/chat/completions"

        # Try multiple compatibility modes before giving up.
        requests: list[dict[str, Any]] = [
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "tax_tutor_schema",
                        "strict": True,
                        "schema": schema,
                    },
                },
                "thinking": {"type": "disabled"},
            },
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
            },
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"{prompt}\n\nReturn ONLY valid JSON matching this schema exactly:\n"
                            f"{json.dumps(schema, ensure_ascii=True)}"
                        ),
                    }
                ],
                "thinking": {"type": "disabled"},
            },
        ]

        last_error = "unknown provider error"
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        for payload in requests:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120, context=ssl_context) as response:
                    raw = response.read().decode("utf-8")
                data = json.loads(raw)
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError("Provider returned no choices.")
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("Provider returned empty message content.")
                text = content.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
                    text = re.sub(r"\s*```$", "", text).strip()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    start = text.find("{")
                    end = text.rfind("}")
                    if start >= 0 and end > start:
                        return json.loads(text[start : end + 1])
                    raise
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = f"OpenAI-compatible provider HTTP {exc.code}: {body[:300]}"
            except Exception as exc:
                last_error = str(exc)
                continue

        raise RuntimeError(last_error)

    def _fallback_response(self, error_message: str, schema_path: Path) -> dict[str, Any]:
        if schema_path == self.grade_schema_path:
            return {
                "overall_summary": "Local grading mode was used. Use the ideal answers as your study guide.",
                "question_feedback": [
                    {
                        "question_number": 1,
                        "verdict": "not graded",
                        "explanation": "Local grading fallback was used.",
                        "ideal_answer": "Review the lesson text and source chunks, then try again.",
                    },
                    {
                        "question_number": 2,
                        "verdict": "not graded",
                        "explanation": "The grading model did not return cleanly for this answer.",
                        "ideal_answer": "Use the source chunks and the lesson summary to restate the concept.",
                    },
                    {
                        "question_number": 3,
                        "verdict": "not graded",
                        "explanation": "The grading model did not return cleanly for this answer.",
                        "ideal_answer": "Try again after rereading the lesson.",
                    },
                ],
                "next_step": "Reread the lesson summary and source evidence, then resubmit your answers.",
            }
        return {
            "card_type": "lesson",
            "title": "Textbook-Based Lesson",
            "subtitle": "Generated from your local textbook data.",
            "intro": "This lesson is running in local textbook mode.",
            "scope_note": "This is only one lesson card, not the whole chapter.",
            "teaching_points": [
                "Use the source chunk previews below as the main study material.",
                "Read the headings first so you know the shape of the topic.",
                "Restate each rule in your own words after each paragraph.",
                "Write a 2-line summary before moving to the quiz.",
                "Use quiz feedback to strengthen your notes.",
            ],
            "worked_example": [
                "Read one paragraph slowly.",
                "Pause and restate it in plain English.",
                "Write one tiny example of your own.",
                "Check that example against the source chunk text.",
            ],
            "flashcards": [
                {"front": "What is the main idea of this lesson?", "back": "It is the key rule or concept described in the source chunks."},
                {"front": "How would you say this rule in plain English?", "back": "Use your own words instead of repeating the textbook."},
                {"front": "What example helps this idea make sense?", "back": "Pick a simple real-life situation that matches the rule."},
            ],
            "quiz_questions": [
                {
                    "prompt": "What is the main rule from this lesson?",
                    "hint": "Pick the option that states the core rule most directly.",
                    "options": [
                        {"label": "A", "text": "Memorize every sentence word-for-word.", "why": "Memorizing wording is not the same as understanding the rule."},
                        {"label": "B", "text": "Ignore the rule and focus only on examples.", "why": "Examples help, but the rule still needs to be stated clearly."},
                        {"label": "C", "text": "State the main rule in clear, simple language using the source chunks.", "why": "This directly captures the lesson's core rule."},
                        {"label": "D", "text": "Choose the most technical phrase you can find.", "why": "Technical language alone does not show real understanding."},
                        {"label": "E", "text": "Skip the rule and move to the next chapter.", "why": "Skipping the rule means missing the lesson's foundation."},
                    ],
                    "correct_option": "C",
                    "study_answer": "State the main rule in clear, simple language using the source chunks.",
                },
                {
                    "prompt": "Why does that rule matter?",
                    "hint": "Choose the option that explains what the rule affects.",
                    "options": [
                        {"label": "A", "text": "Because it makes the textbook longer.", "why": "Textbook length has nothing to do with why the rule matters."},
                        {"label": "B", "text": "Because it explains what the rule affects and why the chapter uses it.", "why": "This explains the rule's purpose and effect."},
                        {"label": "C", "text": "Because every tax rule is exactly the same.", "why": "Tax rules are not all identical, so this is too broad."},
                        {"label": "D", "text": "Because the answer is never important for practice problems.", "why": "Practice depends on understanding why the rule matters."},
                        {"label": "E", "text": "Because it replaces every other concept in the chapter.", "why": "One rule does not replace the rest of the chapter."},
                    ],
                    "correct_option": "B",
                    "study_answer": "Explain what the rule affects and why the chapter uses it.",
                },
                {
                    "prompt": "Which answer gives the best simple example?",
                    "hint": "Pick the small example that actually matches the rule.",
                    "options": [
                        {"label": "A", "text": "Give a small example that matches the rule from the textbook context.", "why": "A matching example shows how the rule works in practice."},
                        {"label": "B", "text": "Say the rule is confusing and stop there.", "why": "This does not give an example at all."},
                        {"label": "C", "text": "Use an unrelated example from a different chapter.", "why": "An unrelated example does not reinforce this rule."},
                        {"label": "D", "text": "List random numbers with no connection to the rule.", "why": "Numbers without context do not show understanding."},
                        {"label": "E", "text": "Skip examples because examples are not useful.", "why": "Examples are one of the best ways to make a rule stick."},
                    ],
                    "correct_option": "A",
                    "study_answer": "Give a small example that matches the rule from the textbook context.",
                },
            ],
            "memory_trick": "If the card falls back, learn from the source chunks and then try again.",
            "next_step": "Use the chunk previews below, then press Explain Simpler or Teach Me The Next Thing.",
            "citations": [],
        }

    def _finalize_card(self, card: dict[str, Any], lesson: Lesson, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        position, total = self.lesson_positions.get(lesson.lesson_id, (1, 1))

        citations = []
        for citation in card.get("citations", []):
            chunk = self.chunk_lookup.get(citation.get("chunk_id", ""))
            if not chunk:
                continue
            citations.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "chapter_number": chunk["chapter_number"],
                    "pages": self._pages_label(chunk["start_pdf_page"], chunk["end_pdf_page"]),
                    "why_this_chunk": citation.get("why_this_chunk", "").strip() or "Relevant textbook support for this lesson.",
                }
            )
        if not citations:
            citations = [
                {
                    "chunk_id": chunk["chunk_id"],
                    "chapter_number": chunk["chapter_number"],
                    "pages": self._pages_label(chunk["start_pdf_page"], chunk["end_pdf_page"]),
                    "why_this_chunk": "Retrieved as the most relevant textbook support for this lesson.",
                }
                for chunk in chunks[:2]
            ]

        source_chunks = [
            {
                "chunk_id": chunk["chunk_id"],
                "chapter_number": chunk["chapter_number"],
                "chapter_title": chunk["chapter_title"],
                "pages": self._pages_label(chunk["start_pdf_page"], chunk["end_pdf_page"]),
                "headings": chunk.get("headings", []),
                "preview": chunk["text"][:1100].strip(),
            }
            for chunk in chunks
        ]

        flashcards: list[dict[str, Any]] = []
        for index, flashcard in enumerate(card.get("flashcards", [])[:3], start=1):
            flashcards.append(
                {
                    "card_id": self._flashcard_id(lesson.lesson_id, flashcard.get("front", ""), index),
                    "front": flashcard.get("front", "").strip(),
                    "back": flashcard.get("back", "").strip(),
                }
            )
        if len(flashcards) < 3:
            while len(flashcards) < 3:
                index = len(flashcards) + 1
                front = f"What should you remember from {lesson.title}?"
                back = "Summarize the rule in plain English and connect it to a simple example."
                flashcards.append({"card_id": self._flashcard_id(lesson.lesson_id, front, index), "front": front, "back": back})

        quiz_questions: list[dict[str, Any]] = []
        for index, item in enumerate(card.get("quiz_questions", [])[:5], start=1):
            correct_option = str(item.get("correct_option", "")).strip().upper()
            if correct_option not in QUIZ_OPTION_LABELS:
                correct_option = QUIZ_OPTION_LABELS[0]
            options = self._normalize_quiz_options(item.get("options", []), correct_option=correct_option)
            prompt_text = item.get("prompt", "").strip()
            study_answer = item.get("study_answer", "").strip()
            quiz_questions.append(
                {
                    "question_id": self._quiz_question_id(
                        lesson.lesson_id,
                        prompt_text,
                        options,
                        correct_option,
                        study_answer,
                    ),
                    "prompt": prompt_text,
                    "hint": item.get("hint", "").strip(),
                    "options": options,
                    "correct_option": correct_option,
                    "study_answer": study_answer,
                }
            )
        if len(quiz_questions) < 3:
            defaults = [
                "What is the main rule from this lesson?",
                "Why does that rule matter?",
                "Give a simple example that fits the rule.",
            ]
            study_answers = [
                "State the lesson's main rule in plain English.",
                "Explain why that rule matters in this topic.",
                "Give a small example that follows the rule.",
            ]
            default_options = [
                [
                    "This option directly states the lesson's main rule in plain English.",
                    "This option changes the rule into something else.",
                    "This option ignores the actual concept.",
                    "This option focuses on a minor detail instead of the rule.",
                    "This option contradicts the lesson.",
                ],
                [
                    "This option explains why the rule matters in the topic.",
                    "This option says the rule does not matter.",
                    "This option changes the question to a different concept.",
                    "This option gives a random fact with no link to the rule.",
                    "This option skips the effect of the rule entirely.",
                ],
                [
                    "This option gives a small example that actually fits the rule.",
                    "This option gives an unrelated example.",
                    "This option repeats the question without answering it.",
                    "This option gives numbers with no explanation.",
                    "This option describes the opposite of the rule.",
                ],
            ]
            while len(quiz_questions) < 3:
                index = len(quiz_questions) + 1
                options = [
                    {
                        "label": label,
                        "text": text,
                        "why": "This option matches the lesson." if label == "A" else "This option does not line up with the lesson's rule.",
                    }
                    for label, text in zip(QUIZ_OPTION_LABELS, default_options[index - 1], strict=False)
                ]
                quiz_questions.append(
                    {
                        "question_id": self._quiz_question_id(
                            lesson.lesson_id,
                            defaults[index - 1],
                            options,
                            "A",
                            study_answers[index - 1],
                        ),
                        "prompt": defaults[index - 1],
                        "hint": "Choose the best answer.",
                        "options": options,
                        "correct_option": "A",
                        "study_answer": study_answers[index - 1],
                    }
                )

        return {
            "lesson_id": lesson.lesson_id,
            "lesson_kind": lesson.lesson_kind,
            "chapter_number": lesson.chapter_number,
            "chapter_title": lesson.chapter_title,
            "card_type": card.get("card_type", lesson.lesson_kind).strip(),
            "title": card.get("title", lesson.title).strip(),
            "subtitle": card.get("subtitle", f"Chapter {lesson.chapter_number}: {lesson.chapter_title}").strip(),
            "exam_day_why": card.get("exam_day_why", f"On exam day, this lesson helps you avoid traps in {lesson.chapter_title.lower()} and pick the correct rule quickly.").strip(),
            "intro": card.get("intro", "").strip(),
            "scope_note": card.get("scope_note", f"This is lesson {position} of {total} in Chapter {lesson.chapter_number}, not the whole chapter.").strip(),
            "teaching_points": [item.strip() for item in card.get("teaching_points", []) if item.strip()],
            "worked_example": [item.strip() for item in card.get("worked_example", []) if item.strip()],
            "flashcards": flashcards,
            "quiz_questions": quiz_questions,
            "quiz_feedback": None,
            "memory_trick": card.get("memory_trick", "").strip(),
            "next_step": card.get("next_step", "").strip(),
            "citations": citations,
            "source_chunks": source_chunks,
            "coverage": {
                "position_in_chapter": position,
                "chapter_lesson_total": total,
            },
            "reopen_lesson_id": lesson.lesson_id if lesson.lesson_id in self.lesson_lookup else None,
        }

    def _build_cumulative_mini_quiz(self, state: dict[str, Any], current_lesson_id: str) -> list[dict[str, Any]]:
        prior_ids = [lesson_id for lesson_id in state["completed_lessons"] if lesson_id != current_lesson_id and lesson_id in self.lesson_lookup]
        if not prior_ids:
            return []
        picks = prior_ids[-2:]
        items: list[dict[str, Any]] = []
        for lesson_id in picks:
            lesson = self.lesson_lookup[lesson_id]
            options = [
                {"label": "A", "text": f"Apply {lesson.title} when the facts match its trigger."},
                {"label": "B", "text": "Ignore trigger words and choose the longest option."},
                {"label": "C", "text": "Use the first chapter rule you remember."},
                {"label": "D", "text": "Skip rule matching and estimate from memory only."},
                {"label": "E", "text": "Choose whichever option has numbers."},
            ]
            items.append(
                {
                    "question_id": f"cum-{lesson.lesson_id}",
                    "prompt": f"Cumulative check: when should you apply the rule from '{lesson.title}'?",
                    "correct_option": "A",
                    "study_answer": f"Apply the rule only when its trigger conditions are present: {lesson.learning_goal}.",
                    "options": options,
                }
            )
        return items

    def _flashcard_id(self, lesson_id: str, prompt: str, index: int) -> str:
        raw = json.dumps({"lesson_id": lesson_id, "prompt": prompt, "index": index}, sort_keys=True)
        return "fc-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]

    def _quiz_question_id(
        self,
        lesson_id: str,
        prompt: str,
        options: list[dict[str, str]],
        correct_option: str,
        study_answer: str,
    ) -> str:
        raw = json.dumps(
            {
                "lesson_id": lesson_id,
                "prompt": prompt,
                "options": [{key: option.get(key, "") for key in ("label", "text", "why")} for option in options],
                "correct_option": correct_option,
                "study_answer": study_answer,
            },
            sort_keys=True,
        )
        return "qq-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]

    def _normalize_quiz_options(self, raw_options: list[Any], correct_option: str = "A") -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        for index, raw_option in enumerate(raw_options[:5]):
            label = QUIZ_OPTION_LABELS[index]
            if isinstance(raw_option, dict):
                text = str(raw_option.get("text", "") or raw_option.get("option", "")).strip()
                option_label = str(raw_option.get("label", "")).strip().upper() or label
                why = str(raw_option.get("why", "") or raw_option.get("rationale", "")).strip()
                if option_label not in QUIZ_OPTION_LABELS:
                    option_label = label
            else:
                text = str(raw_option).strip()
                option_label = label
                why = ""
            if text:
                options.append({"label": option_label, "text": text, "why": why})

        while len(options) < 5:
            label = QUIZ_OPTION_LABELS[len(options)]
            options.append({"label": label, "text": f"Fallback option {label}.", "why": ""})

        normalized: list[dict[str, str]] = []
        for label in QUIZ_OPTION_LABELS:
            option = next((opt for opt in options if opt["label"] == label), None) or {"label": label, "text": f"Fallback option {label}.", "why": ""}
            why = option.get("why", "").strip()
            if not why:
                why = (
                    "This option states the rule that matches the lesson."
                    if label == correct_option
                    else "This option sounds plausible, but it does not match the exact rule from the lesson."
                )
            normalized.append({"label": label, "text": option["text"], "why": why})
        return normalized

    def _option_text(self, question: dict[str, Any], label: str) -> str:
        for option in question.get("options", []):
            if str(option.get("label", "")).strip().upper() == label:
                return str(option.get("text", "")).strip()
        return ""

    def _option_why(self, question: dict[str, Any], label: str) -> str:
        for option in question.get("options", []):
            if str(option.get("label", "")).strip().upper() == label:
                return str(option.get("why", "")).strip()
        return ""

    def _mistake_entry_id(self, lesson_id: str, question_id: str) -> str:
        raw = f"{lesson_id}:{question_id}"
        return "mistake-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]

    def _reopen_lesson_id_for_card(
        self,
        state: dict[str, Any],
        card: dict[str, Any],
        question_id: str,
    ) -> str | None:
        questions = card.get("quiz_questions", [])
        question = next((item for item in questions if item.get("question_id") == question_id), None)
        reopen_lesson_id = (
            (question or {}).get("reopen_lesson_id")
            or card.get("reopen_lesson_id")
            or state.get("current_lesson_id")
        )
        if reopen_lesson_id in self.lesson_lookup:
            return reopen_lesson_id
        lesson_id = card.get("lesson_id")
        if lesson_id in self.lesson_lookup:
            return lesson_id
        return None

    def _record_quiz_results(self, state: dict[str, Any], card: dict[str, Any]) -> None:
        lesson_id = card.get("lesson_id", "")
        if not lesson_id:
            return
        feedback = card.get("quiz_feedback") or {}
        question_feedback = feedback.get("question_feedback", [])
        question_lookup = {question.get("question_id"): question for question in card.get("quiz_questions", [])}
        performance = state["lesson_performance"].setdefault(
            lesson_id,
            {
                "attempts": 0,
                "best_correct_count": 0,
                "last_correct_count": 0,
                "last_total_questions": 0,
                "last_quizzed_at": None,
                "completed_at": None,
            },
        )
        performance["attempts"] = int(performance.get("attempts", 0) or 0) + 1
        performance["last_correct_count"] = int(feedback.get("correct_count", 0) or 0)
        performance["last_total_questions"] = int(feedback.get("total_questions", len(question_feedback)) or len(question_feedback))
        performance["best_correct_count"] = max(int(performance.get("best_correct_count", 0) or 0), performance["last_correct_count"])
        performance["last_quizzed_at"] = self._now()

        notebook: list[dict[str, Any]] = state["mistake_notebook"]
        notebook_by_id = {entry.get("entry_id"): entry for entry in notebook}
        for item in question_feedback:
            question_id = item.get("question_id", f"q{item.get('question_number', 0)}")
            question = question_lookup.get(question_id, {})
            source_entry_id = question.get("source_entry_id")
            entry_id = source_entry_id or self._mistake_entry_id(lesson_id, question_id)
            reopen_lesson_id = (
                question.get("reopen_lesson_id")
                or self._reopen_lesson_id_for_card(state, card, question_id)
            )
            existing_entry = notebook_by_id.get(source_entry_id) if source_entry_id else notebook_by_id.get(entry_id)
            if item.get("verdict") == "correct":
                if entry_id in notebook_by_id:
                    notebook_by_id[entry_id]["resolved"] = True
                    notebook_by_id[entry_id]["resolved_at"] = self._now()
                continue
            taxonomy = self._classify_mistake_taxonomy(question, item)
            notebook_by_id[entry_id] = {
                "entry_id": entry_id,
                "lesson_id": (
                    existing_entry.get("lesson_id")
                    if existing_entry
                    else (reopen_lesson_id if reopen_lesson_id in self.lesson_lookup else lesson_id)
                ),
                "lesson_title": (
                    existing_entry.get("lesson_title")
                    if existing_entry
                    else (
                        self.lesson_lookup[reopen_lesson_id].title
                        if reopen_lesson_id in self.lesson_lookup
                        else card.get("title", "")
                    )
                ),
                "chapter_number": (
                    existing_entry.get("chapter_number")
                    if existing_entry
                    else (
                        self.lesson_lookup[reopen_lesson_id].chapter_number
                        if reopen_lesson_id in self.lesson_lookup
                        else card.get("chapter_number")
                    )
                ),
                "chapter_title": (
                    existing_entry.get("chapter_title")
                    if existing_entry
                    else (
                        self.lesson_lookup[reopen_lesson_id].chapter_title
                        if reopen_lesson_id in self.lesson_lookup
                        else card.get("chapter_title", "")
                    )
                ),
                "question_id": existing_entry.get("question_id") if existing_entry else question_id,
                "reopen_lesson_id": reopen_lesson_id,
                "prompt": question.get("prompt", ""),
                "selected_option": item.get("selected_option", ""),
                "selected_text": item.get("selected_text", ""),
                "correct_option": item.get("correct_option", ""),
                "correct_text": item.get("correct_text", ""),
                "why_selected_wrong": item.get("why_selected_wrong", ""),
                "why_correct_right": item.get("why_correct_right", ""),
                "taxonomy": taxonomy,
                "created_at": self._now(),
                "resolved": False,
                "resolved_at": None,
            }
        state["mistake_notebook"] = sorted(notebook_by_id.values(), key=lambda item: item.get("created_at", ""), reverse=True)[:120]

    def _mark_lesson_completed(self, state: dict[str, Any], lesson_id: str) -> None:
        performance = state["lesson_performance"].setdefault(
            lesson_id,
            {
                "attempts": 0,
                "best_correct_count": 0,
                "last_correct_count": 0,
                "last_total_questions": 0,
                "last_quizzed_at": None,
                "completed_at": None,
            },
        )
        performance["completed_at"] = self._now()

    def _merge_flashcards(self, state: dict[str, Any], lesson: Lesson, card: dict[str, Any]) -> None:
        flashcards = state["flashcards"]
        now = self._now()
        for flashcard in card.get("flashcards", []):
            if flashcard["card_id"] in flashcards:
                continue
            flashcards[flashcard["card_id"]] = {
                "card_id": flashcard["card_id"],
                "lesson_id": lesson.lesson_id,
                "lesson_title": card["title"],
                "chapter_number": lesson.chapter_number,
                "chapter_title": lesson.chapter_title,
                "front": flashcard["front"],
                "back": flashcard["back"],
                "reps": 0,
                "interval_days": 0,
                "ease_factor": 2.3,
                "due_at": now,
                "last_reviewed_at": None,
            }

    def _due_flashcards(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        now = self._now_dt()
        unresolved_by_lesson: dict[str, int] = {}
        for entry in self._unresolved_mistakes(state):
            lesson_id = entry.get("reopen_lesson_id") or entry.get("lesson_id")
            if lesson_id:
                unresolved_by_lesson[lesson_id] = unresolved_by_lesson.get(lesson_id, 0) + 1
        due: list[dict[str, Any]] = []
        for flashcard in state["flashcards"].values():
            due_at = datetime.fromisoformat(flashcard["due_at"])
            if due_at <= now:
                due_item = dict(flashcard)
                lesson_penalty = unresolved_by_lesson.get(flashcard.get("lesson_id", ""), 0)
                reps = int(flashcard.get("reps", 0) or 0)
                age_hours = max(0.0, (now - due_at).total_seconds() / 3600.0)
                due_item["_priority"] = (lesson_penalty * 8.0) + age_hours - (reps * 0.5)
                due.append(due_item)
        due.sort(key=lambda item: item.get("_priority", 0), reverse=True)
        return due

    def _next_due_flashcard(self, state: dict[str, Any]) -> dict[str, Any] | None:
        due = self._due_flashcards(state)
        if not due:
            return None
        flashcard = due[0]
        return {
            "card_id": flashcard["card_id"],
            "front": flashcard["front"],
            "back": flashcard["back"],
            "lesson_title": flashcard["lesson_title"],
            "chapter_number": flashcard["chapter_number"],
            "chapter_title": flashcard["chapter_title"],
            "due_label": "Due now",
        }

    def _rate_flashcard(self, state: dict[str, Any], card_id: str, rating: str) -> None:
        if card_id not in state["flashcards"]:
            raise ValueError("That flashcard could not be found.")
        if rating not in {"again", "hard", "good", "easy"}:
            raise ValueError("Use Again, Hard, Good, or Easy for flashcard review.")

        flashcard = state["flashcards"][card_id]
        reps = int(flashcard.get("reps", 0))
        interval = int(flashcard.get("interval_days", 0))
        ease = float(flashcard.get("ease_factor", 2.3))
        now = self._now_dt()

        if rating == "again":
            next_due = now + timedelta(minutes=10)
            interval = 0
            ease = max(1.3, ease - 0.2)
        elif rating == "hard":
            ease = max(1.3, ease - 0.08)
            interval = 1 if reps == 0 else max(1, round(max(interval, 1) * max(1.2, ease - 0.15)))
            next_due = now + timedelta(days=interval)
        elif rating == "good":
            interval = 2 if reps == 0 else max(2, round(max(interval, 1) * ease))
            next_due = now + timedelta(days=interval)
        else:
            ease = min(2.8, ease + 0.12)
            interval = 4 if reps == 0 else max(4, round(max(interval, 1) * (ease + 0.35)))
            next_due = now + timedelta(days=interval)

        flashcard["reps"] = reps + 1
        flashcard["interval_days"] = interval
        flashcard["ease_factor"] = round(ease, 2)
        flashcard["last_reviewed_at"] = now.isoformat()
        flashcard["due_at"] = next_due.isoformat()

    def _classify_mistake_taxonomy(self, question: dict[str, Any], feedback_item: dict[str, Any]) -> str:
        selected = str(feedback_item.get("selected_option", "")).strip().upper()
        correct = str(feedback_item.get("correct_option", "")).strip().upper()
        selected_text = self._option_text(question, selected).lower()
        correct_text = self._option_text(question, correct).lower()
        prompt_text = str(question.get("prompt", "")).lower()
        if any(word in prompt_text for word in ("calculate", "compute", "amount", "basis", "rate", "deduction", "credit")):
            return "calculation_error"
        if any(word in selected_text for word in ("always", "never", "all", "none")):
            return "overgeneralization"
        if selected and correct and selected_text and correct_text and selected_text[:18] == correct_text[:18]:
            return "reading_precision"
        if "except" in prompt_text or "not" in prompt_text:
            return "question_misread"
        return "concept_confusion"

    def _build_teach_back_card(self, state: dict[str, Any], response_text: str) -> dict[str, Any]:
        lesson = self._require_current_lesson(state)
        chunks = self.search_chunks(
            lesson.retrieval_query,
            chapter_numbers={lesson.chapter_number},
            top_k=4,
        )
        expected_terms = self._keyword_tokens(" ".join(chunk["text"][:400] for chunk in chunks))
        expected_core = list(dict.fromkeys(expected_terms))[:30]
        response_tokens = set(self._keyword_tokens(response_text))
        overlap = [token for token in expected_core if token in response_tokens]
        ratio = (len(overlap) / max(1, len(expected_core))) * 100

        if ratio >= 45:
            verdict = "Strong"
            next_step = "Great teach-back. Move to a mixed review or continue to the next lesson."
        elif ratio >= 25:
            verdict = "Developing"
            next_step = "Good start. Tighten your explanation by naming rule triggers and one numeric-style example."
        else:
            verdict = "Needs Focus"
            next_step = "Re-read the lesson and restate the rule in three sentences: trigger, calculation, and outcome."

        missing = [token for token in expected_core if token not in response_tokens][:8]
        teach_card = {
            "card_type": "teach_back",
            "title": f"Teach-Back Feedback: {lesson.title}",
            "subtitle": f"Chapter {lesson.chapter_number}: {lesson.chapter_title}",
            "intro": f"Teach-back rating: {verdict} ({round(ratio)}% concept coverage).",
            "scope_note": "This feedback scores your own explanation against expected lesson concepts.",
            "teaching_points": [
                f"Concept tokens matched: {', '.join(overlap[:8]) or 'none yet'}",
                f"Missing high-signal terms: {', '.join(missing) or 'none'}",
                "A strong teach-back names the rule trigger, the tax effect, and one concrete example.",
                "Avoid vague wording; use direct tax terms from the lesson.",
                "State one common mistake and why it is wrong.",
            ],
            "worked_example": [
                "Sentence 1: Define the rule in plain language.",
                "Sentence 2: State when the rule applies (trigger).",
                "Sentence 3: Show the tax effect with a tiny numeric example.",
                "Sentence 4: Note one trap and the correction.",
            ],
            "flashcards": [
                {"front": "What trigger makes this lesson's rule apply?", "back": "State the condition that activates the rule."},
                {"front": "What is the core tax effect?", "back": "State what changes: taxable amount, timing, basis, or rate."},
                {"front": "What is one common trap?", "back": "Name one wrong assumption and how to correct it."},
            ],
            "quiz_questions": [
                {
                    "prompt": "Which teach-back sentence quality is strongest?",
                    "hint": "Pick the sentence that includes trigger and tax effect.",
                    "options": [
                        {"label": "A", "text": "The rule exists and taxes are important.", "why": "Too vague; no trigger or effect."},
                        {"label": "B", "text": "When condition X occurs, amount Y is included/excluded because of rule Z.", "why": "Best: trigger, effect, and rule link."},
                        {"label": "C", "text": "I think it depends on everything.", "why": "Too uncertain and non-specific."},
                        {"label": "D", "text": "This chapter has many terms.", "why": "Descriptive but not explanatory."},
                        {"label": "E", "text": "The answer is usually the longest option.", "why": "Test strategy, not concept mastery."},
                    ],
                    "correct_option": "B",
                    "study_answer": "Use trigger + tax effect + rule connection.",
                },
                {
                    "prompt": "What should come after your rule statement?",
                    "hint": "Think applied example.",
                    "options": [
                        {"label": "A", "text": "A concrete example with numbers or facts.", "why": "Correct: demonstrates application."},
                        {"label": "B", "text": "A quote from memory only.", "why": "Memorization without application is weak."},
                        {"label": "C", "text": "An unrelated chapter summary.", "why": "Off-topic."},
                        {"label": "D", "text": "No example is needed.", "why": "Examples are essential for mastery."},
                        {"label": "E", "text": "Only a definition list.", "why": "Definitions alone are not enough."},
                    ],
                    "correct_option": "A",
                    "study_answer": "Follow the rule with a concrete applied example.",
                },
                {
                    "prompt": "How do you make a teach-back exam-ready?",
                    "hint": "Focus on precision and traps.",
                    "options": [
                        {"label": "A", "text": "Keep it broad so it fits everything.", "why": "Broad phrasing misses test precision."},
                        {"label": "B", "text": "Include one common trap and the correction.", "why": "Correct: improves precision under pressure."},
                        {"label": "C", "text": "Avoid naming the rule to save time.", "why": "Naming the rule is core to accuracy."},
                        {"label": "D", "text": "Use only memory tricks.", "why": "Memory tricks help but do not replace logic."},
                        {"label": "E", "text": "Skip cause-and-effect wording.", "why": "Cause/effect wording is important for tax reasoning."},
                    ],
                    "correct_option": "B",
                    "study_answer": "Name a common trap and the correction for exam precision.",
                },
            ],
            "memory_trick": "Teach-back formula: Trigger -> Tax Effect -> Example -> Trap.",
            "next_step": next_step,
            "citations": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "why_this_chunk": "Source used to score your teach-back concept coverage.",
                }
                for chunk in chunks[:2]
            ],
        }
        finalized = self._finalize_card(teach_card, lesson, chunks)
        finalized["teach_back_feedback"] = {
            "verdict": verdict,
            "coverage_percent": round(ratio),
            "matched_terms": overlap[:12],
            "missing_terms": missing,
        }
        return finalized

    def _pages_label(self, start_page: int | None, end_page: int | None) -> str:
        if start_page and end_page and start_page != end_page:
            return f"{start_page}-{end_page}"
        if start_page:
            return str(start_page)
        return "unknown"

    def _cache_key(self, mode: str, lesson_id: str, text: str) -> str:
        raw = json.dumps({"version": CACHE_VERSION, "mode": mode, "lesson_id": lesson_id, "text": text}, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _start_warm_worker(self) -> None:
        if self.warm_worker_started:
            return

        def worker() -> None:
            while True:
                self.warm_event.wait()
                with self.warm_lock:
                    task = self.warm_queue.pop(0) if self.warm_queue else None
                    if not self.warm_queue:
                        self.warm_event.clear()
                if task is None:
                    continue
                lesson, mode, cache_key = task
                try:
                    top_k = 5 if lesson.lesson_kind == "overview" else 4
                    chunks = self.search_chunks(
                        lesson.retrieval_query,
                        chapter_numbers={lesson.chapter_number},
                        top_k=top_k,
                    )
                    prompt = self._lesson_prompt(lesson, mode, chunks)
                    self._run_structured_model(prompt, self.lesson_schema_path, cache_key=cache_key)
                finally:
                    with self.warm_lock:
                        self.warm_inflight.discard(cache_key)

        threading.Thread(target=worker, daemon=True).start()
        self.warm_worker_started = True

    def _warm_lesson_async(self, lesson: Lesson, mode: str = "lesson") -> None:
        cache_key = self._cache_key(mode, lesson.lesson_id, lesson.retrieval_query)
        cache_path = self.cache_root / f"{cache_key}.json"
        if cache_path.exists():
            return
        with self.warm_lock:
            if cache_key in self.warm_inflight:
                return
            self.warm_inflight.add(cache_key)
            self.warm_queue.append((lesson, mode, cache_key))
            self.warm_event.set()

    def _warm_related_async(self, lesson: Lesson, state: dict[str, Any]) -> None:
        for mode in ("simpler", "example", "quiz"):
            self._warm_lesson_async(lesson, mode=mode)

        active_lessons = self._active_lessons(state)
        active_ids = [item.lesson_id for item in active_lessons]
        if lesson.lesson_id not in active_ids:
            return
        start_index = active_ids.index(lesson.lesson_id) + 1
        for lesson_id in active_ids[start_index : start_index + 3]:
            self._warm_lesson_async(self.lesson_lookup[lesson_id], mode="lesson")

    def _start_initial_warm(self, state: dict[str, Any]) -> None:
        if self.initial_warm_started:
            return
        self.initial_warm_started = True
        current = self._require_current_lesson(state)
        self._warm_related_async(current, state)
        for lesson in self._active_lessons(state)[:8]:
            self._warm_lesson_async(lesson, mode="lesson")

    def _keyword_tokens(self, text: str) -> list[str]:
        tokens = []
        for token in TOKEN_RE.findall(text.lower()):
            if token in LOCAL_GRADE_STOPWORDS:
                continue
            if len(token) <= 2 and not any(char.isdigit() for char in token):
                continue
            tokens.append(token)
        return tokens

    def _compare_answer(self, answer: str, study_answer: str) -> tuple[str, str]:
        answer_tokens = self._keyword_tokens(answer)
        expected_tokens = self._keyword_tokens(study_answer)
        if not expected_tokens:
            return ("partly correct", "This answer could not be checked precisely, so use the study answer to review the main point.")

        answer_set = set(answer_tokens)
        expected_set = set(expected_tokens)
        overlap = len(answer_set & expected_set) / len(expected_set)
        similarity = len(answer_set & expected_set) / max(len(answer_set), 1) if answer_set else 0.0

        missing = [token for token in expected_tokens if token not in answer_set]
        if overlap >= 0.58 or (overlap >= 0.45 and similarity >= 0.45):
            verdict = "correct"
            explanation = "You included the main idea from the lesson. Keep using the same plain-English wording."
        elif overlap >= 0.24 or similarity >= 0.22:
            verdict = "partly correct"
            if missing:
                explanation = f"You have part of it, but your answer would be stronger if it clearly mentioned {', '.join(missing[:3])}."
            else:
                explanation = "You are close, but the answer still needs a more direct statement of the rule."
        else:
            verdict = "incorrect"
            explanation = "This misses the main rule from the lesson. Compare your answer to the study answer and notice the key idea you left out."
        return verdict, explanation
