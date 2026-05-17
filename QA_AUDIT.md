# Tax Tutor App QA Audit

Date: 2026-05-16

## Publish Status

- GitHub repo: https://github.com/bskthefirst/tax-tutor-app
- GitHub Pages URL: https://bskthefirst.github.io/tax-tutor-app/
- Pages status: built
- Current Pages behavior: the root Pages URL serves the rendered `README.md`, not the live tutor app.
- Important hosting limitation: the app is a Python HTTP server with `/api/bootstrap` and `/api/action`. GitHub Pages can only serve static files, so it cannot run the current tutor app without a static export, mocked data, or a separate backend.

## QA Scope

I did not change application code for this audit. I tested a disposable copy of the app at `/tmp/tax-tutor-qa.BiMAPW/tax-tutor-app`, symlinked to the existing textbook asset folder, so the QA flow would not overwrite your real `data/study_state.json`.

Checks performed:

- Python syntax check with `python3 -m py_compile app.py engine.py warm_cache.py`.
- Engine initialization check.
- HTTP checks for `/`, `/static/app.js`, `/api/bootstrap`, unknown paths, and static traversal attempts.
- API error checks for unsupported actions, missing lesson IDs, malformed JSON, weekly goal clamping, and midterm chapter clamping.
- Headless browser E2E pass with Playwright covering dashboard tabs, plan tab, course tab, resume lesson, lesson stage switching, quiz answer/grade flow, complete-and-continue flow, empty question validation, and midterm plan update.

## Passing Results

- Server starts locally on Python 3.10.
- Engine initialized successfully with 25 chapters, 1,355 lesson nodes, and 742 retrieval chunks.
- `/` returns `200 OK` and serves the UI shell.
- `/static/app.js` returns `200 OK`.
- `/api/bootstrap` returns valid JSON and was parsed successfully.
- `/static/../app.py` returns `404`, so the static file path guard blocks simple traversal.
- Unknown GET routes return `404`.
- Unsupported POST action returns `400`.
- Missing lesson ID returns `400`.
- Malformed JSON returns `400`.
- Weekly goal input clamps to the configured maximum of 5.
- Out-of-range midterm chapters clamp back into the valid chapter range.
- Browser E2E produced no JavaScript console errors and no page errors.
- Dashboard tab switching works for Today, Practice, Plan, and Course.
- Lesson resume opens study mode.
- Lesson stage switching works for Learn, Example, Quiz, Review, and Evidence.
- Quiz flow works: selected answers, submitted, and produced graded feedback.
- Complete-and-continue works and advances to the next lesson in the disposable state.
- Empty free-form question submission shows client-side validation.
- Midterm mode update works and reduces the rendered weekly plan.

## Findings

### 1. GitHub Pages is created, but it is not a working hosted tutor

Severity: High

Evidence:

- `https://bskthefirst.github.io/tax-tutor-app/` returns the README-rendered Jekyll page.
- `https://bskthefirst.github.io/tax-tutor-app/static/index.html` returns the app HTML, but it references `/static/styles.css` and `/static/app.js` from the domain root.
- `https://bskthefirst.github.io/static/styles.css` returns `404`.
- `https://bskthefirst.github.io/tax-tutor-app/api/bootstrap` returns `404`.
- Root cause: `static/index.html` uses absolute asset paths at lines 7 and 191, and `static/app.js` calls `/api/bootstrap` and `/api/action` at lines 2158 and 2174. GitHub Pages cannot run those Python endpoints.

Impact: the public URL exists, but users cannot study from it as a live app.

### 2. Fresh public clone is not self-contained

Severity: High

Evidence:

- `engine.py` hardcodes the textbook assets to `app_root.parent / "textlayer-work" / "tutor-assets"` at line 94.
- Engine initialization reads `prompt/tax-tutor-system-prompt.md`, `chapter_index.json`, and `taxation-2025-chunks.jsonl` from that external folder at lines 101-109.
- The pushed repo intentionally excludes generated cache and local study state, and it does not include the external textbook assets.
- There is no `requirements.txt` or equivalent dependency manifest even though the engine imports `numpy` and `sklearn`.

Impact: the repo is public and clean, but another machine cannot run it from the GitHub clone alone.

### 3. Curriculum extraction includes backmatter/index junk as lessons

Severity: High for mastery quality

Evidence:

- Engine reports 1,355 lesson nodes.
- Chapter 25 has 112 lesson nodes, far higher than most chapters.
- I found 54 obvious non-learning lesson nodes, including `A`, `B`, `C`, `Code Index - Z`, `Subject Index - 263A...`, `ADDITIONAL STUDENT RESOURCES`, and many `Return to ... Graphic` lessons.
- Likely source: `_build_curriculum` treats most parsed markdown headings as lessons in `engine.py` lines 192-245, while `_collect_meaningful_headings` and `_classify_heading` only filter a small list of headings in lines 270-337.

Impact: the mastery path eventually asks you to study index/backmatter artifacts instead of tax concepts.

### 4. Full-course weekly plan is too large

Severity: Medium

Evidence:

- `/api/bootstrap` returned about 674 KB in the current saved state.
- The full plan rendered 678 weeks in the API summary and 683 `.week-chip` nodes during the browser run after state changes.
- `_build_weekly_plan` creates every week for every active lesson in `engine.py` lines 845-874.
- `renderWeeklyPlan` renders all weeks into DOM in `static/app.js` lines 442-460.

Impact: Plan view is technically functional but overwhelming, slow to scan, and expensive to render. It also makes the API payload much larger than the dashboard needs.

### 5. Bootstrap sends more data than the first screen needs

Severity: Medium

Evidence:

- `/api/bootstrap` includes all chapters, all lessons, all weekly-plan entries, current card, source chunks, flashcard counts, and dashboard state in one response.
- `render()` immediately renders nearly every major panel from that payload in `static/app.js` lines 2148-2154.

Impact: initial load is heavier than necessary and will get worse as progress, flashcards, and mistake history grow.

### 6. Model-backed actions can block a request for up to 180 seconds

Severity: Medium

Evidence:

- `_run_structured_model` shells out to `codex exec` synchronously and waits up to 180 seconds in `engine.py` lines 1574-1626.

Impact: cache misses can make "next lesson", examples, review, exams, or questions feel frozen. The UI has a loading status, but the backend request itself is synchronous.

### 7. API error messages expose raw technical details

Severity: Low locally, higher if hosted publicly

Evidence:

- Malformed JSON returns the raw parser error: `Expecting property name enclosed in double quotes: line 1 column 2 (char 1)`.
- The generic exception handler returns `Unexpected error: {exc}` in `app.py` lines 57-58.

Impact: this is acceptable for a local study tool, but it is not production-friendly.

### 8. No automated regression test suite exists yet

Severity: Medium

Evidence:

- The repo has no committed unit tests, API tests, or browser tests.
- QA had to be performed manually with ad hoc shell and Playwright checks.

Impact: future UI/curriculum changes could silently break quiz flow, plan rendering, or course navigation.

## 10 Improvements To Better Support Mastery

1. Build a real deployment split: either a hosted Python backend for the live tutor, or a GitHub Pages static demo that uses prebuilt sample JSON and clearly says it is a demo.
2. Make the app clone-runnable: add dependency manifest, setup instructions, and an asset path config such as `TAX_TUTOR_ASSETS_ROOT`.
3. Clean the curriculum extractor so it excludes code indexes, subject indexes, graphic-return links, additional resources, and one-letter backmatter headings.
4. Rebuild the mastery model around learning objectives, not just lesson order: show LO coverage, weak LOs, strong LOs, and prerequisite gaps.
5. Add adaptive diagnostics before each chapter so the app can skip what you already know and focus on weak concepts.
6. Add calculation/workpaper practice for tax math: guided tables, taxable income build-ups, rate calculations, basis schedules, depreciation, and credit computations.
7. Add spaced retrieval scheduling that mixes flashcards, short answers, and old missed questions based on decay, not just due count.
8. Upgrade the mistake notebook into an error taxonomy: conceptual error, missed fact, calculation error, reading issue, and exam-trick pattern.
9. Add cumulative exam simulator modes with timed mixed sets, confidence ratings, review screens, and score by chapter/LO.
10. Add a "teach it back" mode where you explain a concept in your own words and the app grades for missing elements, misconceptions, and citation-backed corrections.
