from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import TaxTutorEngine  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precompute Tax Tutor lesson caches.")
    parser.add_argument("--start-chapter", type=int, default=1)
    parser.add_argument("--end-chapter", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0, help="Optional limit on number of lessons to warm.")
    parser.add_argument(
        "--modes",
        default="lesson",
        help="Comma-separated lesson modes to warm. Example: lesson,simpler,example,quiz",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app_root = Path(__file__).resolve().parent
    engine = TaxTutorEngine(app_root)

    lessons = [
        lesson
        for lesson in engine.lessons
        if args.start_chapter <= lesson.chapter_number <= args.end_chapter
    ]
    if args.limit > 0:
        lessons = lessons[: args.limit]
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    if not modes:
        modes = ["lesson"]

    total = len(lessons) * len(modes)
    if total == 0:
        print("No lessons matched the requested range.")
        return

    print(
        f"Warming {len(lessons)} lessons across modes {', '.join(modes)} "
        f"from chapters {args.start_chapter}-{args.end_chapter}..."
    )
    warmed = 0
    skipped = 0
    started = time.time()

    index = 0
    for lesson in lessons:
        for mode in modes:
            index += 1
            top_k = 5 if lesson.lesson_kind == "overview" else 4
            cache_key = engine._cache_key(mode, lesson.lesson_id, lesson.retrieval_query)
            cache_path = engine.cache_root / f"{cache_key}.json"
            if cache_path.exists():
                skipped += 1
                print(f"[{index}/{total}] skip  {mode:<7} {lesson.lesson_id}  {lesson.title}")
                continue

            chunks = engine.search_chunks(
                lesson.retrieval_query,
                chapter_numbers={lesson.chapter_number},
                top_k=top_k,
            )
            prompt = engine._lesson_prompt(lesson, mode, chunks)
            engine._run_structured_model(prompt, engine.lesson_schema_path, cache_key=cache_key)
            warmed += 1
            print(f"[{index}/{total}] warm  {mode:<7} {lesson.lesson_id}  {lesson.title}")

    elapsed = round(time.time() - started, 1)
    print(f"Done in {elapsed}s. Warmed: {warmed}. Skipped existing: {skipped}.")


if __name__ == "__main__":
    main()
