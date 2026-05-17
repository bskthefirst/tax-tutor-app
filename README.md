# Tax Tutor App

This is a small local teaching app built around the cleaned `Taxation of Individuals and Business Entities 2025` textbook assets.

## What it does

- teaches through a finer lesson map built from chapter sections, key facts, reviews, and practice blocks
- lets you press `Teach Me The Next Thing` instead of inventing questions
- uses retrieval over the textbook chunks
- asks Codex to turn the retrieved chunks into a short beginner-friendly lesson
- gives every lesson 3 flashcards
- gives every lesson a 3-question short-answer quiz that you can submit for grading and explanation
- supports a chapter-based weekly plan and a configurable midterm chapter range
- tracks study progress locally

## Run it

```bash
cd "/Users/suhyun/Documents/Auditing Poly/tax-tutor-app"
python3 -m pip install -r requirements.txt
python3 app.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Environment variables

- `TAX_TUTOR_ASSETS_ROOT`: optional absolute path to `tutor-assets`
- `TAX_TUTOR_DATA_ROOT`: optional absolute path for runtime data (`study_state.json`, cache, schemas)
- `PORT`: optional HTTP port for hosted environments
- `HOST`: optional host bind address (defaults to `0.0.0.0` when `PORT` is set, else `127.0.0.1`)

Example hosted-style run:

```bash
export TAX_TUTOR_ASSETS_ROOT="/path/to/textlayer-work/tutor-assets"
export PORT=10000
python3 app.py
```

## Deploy backend on Render

`render.yaml` is included for a basic free-tier web service:

```bash
cd "/Users/suhyun/Documents/Auditing Poly/tax-tutor-app"
git push
```

Then create a new Blueprint service in Render from this repository. Set `TAX_TUTOR_ASSETS_ROOT` in Render so it points to a mounted assets location that contains:

- `prompt/tax-tutor-system-prompt.md`
- `chapter_index.json`
- `taxation-2025-chunks.jsonl`
- `chapters/`

## Warm the cache

If you want the early lessons ready before a study session:

```bash
cd "/Users/suhyun/Documents/Auditing Poly/tax-tutor-app"
python3 warm_cache.py --start-chapter 1 --end-chapter 1
```

You can also precompute a wider range, such as the first two chapters:

```bash
python3 warm_cache.py --start-chapter 1 --end-chapter 2
```

## Main actions

- `Teach Me The Next Thing`
- `I Got This, Next Lesson`
- `Review This Week`
- `Explain Simpler`
- `Give Another Example`
- `Quiz Me`
- submit 3 short quiz answers for grading
- review due flashcards with `Again / Hard / Good / Easy`
- free-form question box

## Files

- [app.py](/Users/suhyun/Documents/Auditing%20Poly/tax-tutor-app/app.py): local HTTP server
- [engine.py](/Users/suhyun/Documents/Auditing%20Poly/tax-tutor-app/engine.py): curriculum, retrieval, Codex calls, progress tracking
- [warm_cache.py](/Users/suhyun/Documents/Auditing%20Poly/tax-tutor-app/warm_cache.py): precompute lesson caches before you study
- [static/index.html](/Users/suhyun/Documents/Auditing%20Poly/tax-tutor-app/static/index.html): UI shell
- [static/app.js](/Users/suhyun/Documents/Auditing%20Poly/tax-tutor-app/static/app.js): browser logic
- [static/styles.css](/Users/suhyun/Documents/Auditing%20Poly/tax-tutor-app/static/styles.css): visual design

## Notes

- Study progress is stored in `data/study_state.json`.
- Generated cards are cached in `data/cache/` so repeat clicks are faster and cheaper.
- If `TAX_TUTOR_ASSETS_ROOT` is not set, the app defaults to `../textlayer-work/tutor-assets` relative to this repo.
- The chapter overview is only the first card in a chapter, not the whole chapter. The chapter lesson list in the sidebar shows the deeper coverage.
