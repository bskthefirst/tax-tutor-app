# Tax Tutor Assets

These files package the cleaned textbook markdown into chapter files and retrieval-ready chunks.

## What is here

- `chapters/`: one Markdown file per textbook chapter
- `chunks/`: one Markdown file per retrieval chunk with metadata front matter
- `taxation-2025-chunks.jsonl`: JSONL export for vector databases or custom RAG pipelines
- `chapter_index.json`: chapter inventory with file names and page spans
- `manifest.json`: top-level build metadata
- `prompt/tax-tutor-system-prompt.md`: a simple beginner-friendly system prompt

## Source

- Markdown source: `/Users/suhyun/Documents/Auditing Poly/textlayer-work/full-book/Taxation of Individuals and Business Entities 2025 -- Brian C_ Spilker, Benjamin C_ Ayers, Troy K_ Lewis, Connie -- 2025, 2025 -- McGraw Hill -- 9781265471422 -- b3af8ff1e7fee57c6b7f0efbab05bcb7 -- Anna’s Archive.md`
- PDF source: `/Users/suhyun/Documents/UIUC MSA/Taxation of Individuals and Business Entities 2025 -- Brian C_ Spilker, Benjamin C_ Ayers, Troy K_ Lewis, Connie -- 2025, 2025 -- McGraw Hill -- 9781265471422 -- b3af8ff1e7fee57c6b7f0efbab05bcb7 -- Anna’s Archive.pdf`

## Recommended use

1. Embed `taxation-2025-chunks.jsonl` or the `chunks/` files into your retrieval system.
2. Use `prompt/tax-tutor-system-prompt.md` as the system instruction.
3. Return the chunk text plus metadata to the model so it can cite chapter and PDF pages.
4. If you need browsing or manual review, open the chapter files in `chapters/`.

## Chunk schema

Each JSONL row includes:

- `chunk_id`
- `chapter_number`
- `chapter_title`
- `chunk_index`
- `start_pdf_page`
- `end_pdf_page`
- `pdf_pages`
- `headings`
- `word_count`
- `text`

## Rebuild

```bash
python3 "/Users/suhyun/Documents/Auditing Poly/textlayer-work/build_tax_tutor_assets.py" \
  --source-markdown "/Users/suhyun/Documents/Auditing Poly/textlayer-work/full-book/Taxation of Individuals and Business Entities 2025 -- Brian C_ Spilker, Benjamin C_ Ayers, Troy K_ Lewis, Connie -- 2025, 2025 -- McGraw Hill -- 9781265471422 -- b3af8ff1e7fee57c6b7f0efbab05bcb7 -- Anna’s Archive.md" \
  --source-pdf "/Users/suhyun/Documents/UIUC MSA/Taxation of Individuals and Business Entities 2025 -- Brian C_ Spilker, Benjamin C_ Ayers, Troy K_ Lewis, Connie -- 2025, 2025 -- McGraw Hill -- 9781265471422 -- b3af8ff1e7fee57c6b7f0efbab05bcb7 -- Anna’s Archive.pdf" \
  --output-root "/Users/suhyun/Documents/Auditing Poly/textlayer-work/tutor-assets" \
  --target-words 850 \
  --max-words 1150
```
