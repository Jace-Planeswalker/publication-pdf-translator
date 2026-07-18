# M5 product specification

Status: implemented research release

> The M5 translation and artifact contracts remain current. M6 supersedes only
> the thin operator surface with an executable adapter, local MCP tools and
> content-bound project control; see `m6-operator-adapter-spec.md`.

## Product decision

The deliverable is a standalone, resumable Python project. BabelDOC is the
only PDF extraction, intermediate-layout, typesetting and layout-restoration
engine. There is no LaTeX or DOCX output path because those formats do not
serve the target outcome: a restored-layout translated PDF.

The Codex Skill and repository plugin are deliberately thin. They invoke,
resume and inspect this project; they do not translate a book in chat, maintain
a second ledger, or bypass a failed gate.

## One-command contract

```bash
pubtrans translate source.pdf \
  --project projects/source-zh \
  --config examples/config.example.json \
  --evidence evidence.json
```

The command proceeds through preparation, document analysis, terminology
research/review, risk-adaptive translation, blind review, adjudication,
conservative Chinese editing, independent verification, whole-document review,
BabelDOC restoration and actual-PDF verification. Repeating the command reuses
content-addressed successful calls and persisted stage results.

Success emits:

- `output/<source>.zh-Hans.verified.pdf`;
- `output/verification-report.json`;
- a `RELEASED` JSON object on stdout.

A real blocker emits structured `BLOCKED` JSON on stderr. A blocked artifact is
recorded for audit but never activated or published as a verified PDF.

## Translation quality path

1. Chunked source-only analysis creates the document brief, sense-specific
   term candidates and unit risk routes.
2. Terminology retrieval treats model memory and search citations as discovery,
   captures actual page excerpts, and requires an isolated reviewer.
3. Code computes terminology confidence and mainstream preference; unsupported
   specialist-sounding forms fall back to the source.
4. R1 units use one lane; semantic, term, formula, modality or ambiguity risk
   uses two isolated lanes.
5. A blind bilingual reviewer sees option text but not candidate provenance.
6. Adjudication selects an unchanged option only when it needs no correction;
   otherwise it synthesizes and resolves serious findings.
7. Chinese editing is conservative and is independently checked for edit
   degradation.
8. A deterministic gate blocks substantially untranslated Chinese targets even
   if a model incorrectly reports PASS.
9. Whole-book review is bounded into recoverable chunks. A final synthesis
   compares compact continuity observations across distant chunks, and cannot
   erase a chunk blocker.

Every governed term is bound to exact source occurrences. Unique invisible-to-
output markers recover exact target spans and fail closed if a model omits,
duplicates, nests or alters the required rendering.

## Provider contract

The production adapter uses the OpenAI Responses API in stateless mode. Strict
JSON schemas are passed through `text.format`; optional terminology discovery
uses hosted web search. Role-specific model overrides allow separate models for
analysis, research, translation, review, adjudication, editing and verification.
Credentials are read only from the configured environment variable and are
never stored in project state.

All remote stages use immutable call descriptors, attempt ledgers, fenced
leases, bounded retries and pre-reserved budgets. Chunk analysis, terminology
research, evidence harvesting and whole-book review calls are independently
replayable after interruption.

## Final artifact gate

The runtime reopens the real BabelDOC output and checks source binding, PDF
integrity, page count and geometry, blank pages, approved target fragments,
protected numbers/URLs/equations, image digests, fonts, replacement glyphs,
text bounds and material overlaps. Only a passing report atomically activates a
content-addressed final PDF. `pubtrans status` re-verifies the active store and
the published output digest; deletion or mutation changes the reported state to
`BLOCKED`.

## Acceptance evidence

The M5 integration suite runs two materially different sources through the
actual BabelDOC high-level pipeline:

- a two-page thermodynamics PDF with a governed scientific term, formula,
  number and image;
- a one-page narrative PDF with a governed literary proper name, title,
  paragraph rhythm and reference continuity.

Both must produce a passing artifact report and extracted Chinese output. A
third run reopens a completed technical project with a client that fails if
called, proving zero model calls on resume. Adversarial tests also cover
unsubstantiated jargon, rare-form bias, same-publisher evidence inflation,
damaged term markers, untranslated text, incomplete provider responses and
cross-chunk call replay.

## Research and license boundary

The project is research-only. Its own code is AGPL-3.0-or-later. Vendored
AIDAterm prompt material remains CC BY-NC 4.0 and is not relicensed. Source
books, translations, model responses, credentials, databases and copyrighted
regression PDFs stay outside Git.
