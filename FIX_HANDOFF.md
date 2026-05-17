# Tax Tutor App Fix Handoff

This file is for the next AI/engineer implementing fixes. The audit evidence is in `QA_AUDIT.md`; this file turns it into an actionable work order.

## Goal

Make Tax Tutor reliably usable by the owner for mastering the tax textbook, with a clear deployment story and a cleaner learning path. Do not optimize for a public marketing site yet; optimize for a working study tool.

## Critical Constraint

The current app is not a static website. It is a Python backend that serves:

- `/`
- `/static/*`
- `/api/bootstrap`
- `/api/action`

GitHub Pages cannot run this backend. Keep GitHub Pages only for docs/demo unless the app is converted into a static-only demo.

Also be careful with textbook assets. The app currently expects assets outside the repo at:

```text
../textlayer-work/tutor-assets
```

Do not commit proprietary textbook text/assets to a public repo unless the user explicitly confirms they have the right to publish them.

## Recommended Implementation Path

### Phase 1: Make the app deployable as a backend

1. Add a dependency manifest.
   - Likely `requirements.txt`.
   - Include at least `numpy` and `scikit-learn`.
   - Verify all imports from `engine.py` and `warm_cache.py`.

2. Make the asset path configurable.
   - Add `TAX_TUTOR_ASSETS_ROOT`.
   - Default can remain `app_root.parent / "textlayer-work" / "tutor-assets"` for local use.
   - On startup, show a clear error if required asset files are missing.

3. Make the server deployment-friendly.
   - Bind to `0.0.0.0` in hosted environments.
   - Accept a `PORT` environment variable.
   - Keep CLI flags working.
   - Example target behavior:

```bash
PORT=10000 python3 app.py
```

4. Choose a hosting target.
   - Render free web service is the simplest first target for a Python hobby backend, but it sleeps after inactivity.
   - Hugging Face Spaces with Docker is another free CPU option, but the UI/domain will feel more like an app demo.
   - Vercel can run Python functions, but this app would need restructuring into serverless routes and persistent state/storage changes.

5. Decide how state should work online.
   - Current state is local JSON at `data/study_state.json`.
   - For personal-only use, a mounted disk or single-user JSON file is acceptable.
   - For multi-device or multi-user use, move state to SQLite/Postgres.

### Phase 2: Fix curriculum quality

1. Add tests that assert the curriculum excludes obvious backmatter.
   - No one-letter lessons like `A`, `B`, `C`.
   - No `Code Index`.
   - No `Subject Index`.
   - No `ADDITIONAL STUDENT RESOURCES`.
   - No titles starting with `Return to `.

2. Update curriculum heading filtering in `engine.py`.
   - Relevant code starts around `_build_curriculum`, `_collect_meaningful_headings`, and `_classify_heading`.
   - Add a helper such as `_should_skip_heading(heading, level, chapter_number)`.

3. Re-run the engine initialization count.
   - Current baseline from audit: 25 chapters, 1,355 lesson nodes, 742 chunks.
   - After cleanup, lesson count should drop.
   - Chapter 25 should no longer have 112 lesson nodes from indexes/backmatter.

### Phase 3: Reduce dashboard payload and plan overload

1. Stop rendering every future week by default.
   - Current full-course view renders about 678+ week cards.
   - Show current week plus the next 8-12 weeks.
   - Add a "show full plan" option only if needed.

2. Reduce `/api/bootstrap` payload.
   - Do not send every lesson in every chapter unless the Course tab asks for it.
   - Do not send the full weekly plan unless the Plan tab asks for it.
   - Consider endpoints like:
     - `/api/bootstrap`
     - `/api/course`
     - `/api/plan`
     - `/api/lesson/<id>`

3. Keep the first screen fast.
   - Dashboard should need current lesson, stats, today assignment, due flashcard count, and a small mastery summary.

### Phase 4: Add regression tests

Add at least these tests before/while changing behavior:

1. Engine boot test.
2. Curriculum skip-list test for backmatter/index headings.
3. API bootstrap returns required dashboard keys.
4. API invalid action returns `400`.
5. Quiz grading works locally for a known card.
6. Playwright smoke test:
   - home loads
   - tabs switch
   - resume opens lesson
   - quiz can be answered and graded
   - complete-and-continue advances

### Phase 5: Improve mastery features

After deployment and cleanup, implement mastery features in this order:

1. Learning-objective mastery map.
2. Chapter diagnostic pretests.
3. Tax calculation/workpaper drills.
4. Spaced retrieval scheduler using misses plus due flashcards.
5. Mistake taxonomy.
6. Cumulative timed exam simulator.
7. Teach-it-back grading mode.

## Acceptance Criteria

- A fresh clone can run after documented setup, without relying on hardcoded absolute paths.
- Hosted app URL opens the working tutor, not just README documentation.
- Public repo does not expose private study state, generated cache, credentials, or proprietary textbook assets without permission.
- Curriculum no longer includes index/backmatter junk lessons.
- Full-course dashboard does not render hundreds of week cards by default.
- Core E2E study flow passes in an automated browser test.

