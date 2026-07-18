# Publication PDF Translator

`publication-pdf-translator` is a research-only, resumable translation compiler
for producing restored-layout Simplified Chinese PDFs. It treats translation as
an auditable build: source identity, terminology evidence, model calls, review,
approvals, rendering and final-PDF checks all have durable state.

> [!WARNING]
> The historical M0 v1 checkpoint is invalidated. Its unit identity depended on
> BabelDOC's random paragraph `debug_id`. Current projects use the clean M0 v2
> prepared-IL contract and cannot migrate or reuse v1 databases.

## Product boundary

- BabelDOC is the only parsing, intermediate-layout, typesetting and PDF
  reconstruction engine.
- This project owns stable units, terminology, translation/review stages,
  recovery, budgets, releases and artifact verification.
- There is no LaTeX or DOCX pipeline: the product target is a restored-layout
  translated PDF.
- The included Skill/plugin is a thin launcher and monitor. It never translates
  a book in chat or keeps a second state ledger.

The BabelDOC provider seam is pinned to the audited
[`feature/document-translation-provider-v2`](https://github.com/Jace-Planeswalker/BabelDOC/tree/feature/document-translation-provider-v2)
fork commit `0b3b03ab1ed29c15245dca49f6cb3afece046f95`.

## Quality pipeline

The one-command runtime performs:

1. deterministic BabelDOC prepared-IL capture and complete unit coverage;
2. chunked source analysis and a source-only document brief;
3. sense-specific terminology research with captured excerpts, counterevidence,
   independent review and code-derived confidence;
4. one or two isolated translation lanes according to semantic risk;
5. blind bilingual review, explicit adjudication and conservative Chinese edit;
6. independent edit-impact verification and deterministic untranslated-text
   blocking;
7. bounded whole-book review plus cross-chunk continuity synthesis;
8. BabelDOC restored-PDF rendering;
9. page, text, protected-anchor, image, font, clipping and overlap gates on the
   actual emitted PDF.

Search snippets and model memory generate candidates; they are never evidence.
Two pages from one publishing host count as one source. Unsupported or obscure
terms retain the source expression instead of manufacturing precision.

## Install and run

Python 3.10–3.13 is supported.

```bash
python -m pip install -e '.[babeldoc]'
cp examples/config.example.json config.json
cp examples/evidence.example.json evidence.json
```

Edit the examples, set the credential environment variable named in
`config.json`, then run:

```bash
export OPENAI_API_KEY='...'
pubtrans translate source.pdf \
  --project projects/source-zh \
  --config config.json \
  --evidence evidence.json
```

To use command-line settings instead of a config file:

```bash
pubtrans translate source.pdf \
  --project projects/source-zh \
  --model '<supported-model>' \
  --source-language en \
  --target-language zh-Hans
```

The same command resumes an interrupted or completed project. Inspect it with:

```bash
pubtrans status projects/source-zh
```

Successful output is written to:

- `projects/source-zh/output/<source>.zh-Hans.verified.pdf`
- `projects/source-zh/output/verification-report.json`

Only a passing, content-addressed artifact is published. If an output PDF is
deleted or changed after release, `status` reports `BLOCKED`.

## Configuration and terminology

Configuration contains model names, role overrides, budgets and the name of a
credential environment variable—never the credential itself. See
[`examples/config.example.json`](examples/config.example.json).

Terminology evidence is a dossier rather than a glossary. See
[`docs/terminology-evidence-format.md`](docs/terminology-evidence-format.md) and
[`examples/evidence.example.json`](examples/evidence.example.json). Hosted web
research can discover sources automatically; manual evidence is useful for
books, standards and paywalled references that cannot be fetched safely.

## Verification

```bash
python -m pip install -e '.[babeldoc,dev]'
ruff check src tests
pytest -q
```

The suite includes contract, adversarial, recovery and actual BabelDOC PDF
tests. The M5 product trials cover a technical document with formula/image and
a narrative document with name/register, plus zero-call completed-project
resume.

## Design and provenance

Start with the [M5 product specification](docs/m5-product-spec.md),
[architecture](docs/architecture.md), and
[research/adoption record](docs/research-notes.md). Earlier invariant layers are
documented in the M0–M4 specifications under `docs/`.

The project adopts or ports mature ideas when quality and license permit it.
AIDAterm research prompts are retained under `third_party/aida-term/` at source
commit `716f2e2532ad391fea206623716394333b1f25b1`; they remain CC BY-NC 4.0 and
are not relicensed. Project code is AGPL-3.0-or-later. Source publications,
translations, API keys, model responses, state databases and copyrighted test
PDFs must remain outside Git.
