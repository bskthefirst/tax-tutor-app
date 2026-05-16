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
python3 app.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

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
- The app expects the packaged tutor assets in `/Users/suhyun/Documents/Auditing Poly/textlayer-work/tutor-assets`.
- The chapter overview is only the first card in a chapter, not the whole chapter. The chapter lesson list in the sidebar shows the deeper coverage.
