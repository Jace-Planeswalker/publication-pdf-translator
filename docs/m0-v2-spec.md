# M0 v2 clean-room specification

Status: normative design draft

Target engine baseline: BabelDOC 0.6.4 (`17480db`)

Contract namespace: `pubtrans.prepared-document/v2`

## Why v1 is invalidated

M0 v1 used BabelDOC's paragraph `debug_id` as part of a supposedly stable
translation-unit identity. BabelDOC 0.6.4 creates that value with
`random.choice()` on every paragraph-discovery run. A second extraction of the
same PDF therefore produces a different unit set. The v1 capture-then-resume
flow cannot meet its core recovery claim.

This v2 is derived from the product requirements below. V1 implementation
types and database tables are not normative; they are retained only as an
adversarial regression source.

## Product boundary

M0 v2 does one job: establish a lossless, resumable, fail-closed handoff
between BabelDOC's prepared document IL and an external translation kernel.
It does not decide translation quality, call a model, or certify a rendered
PDF. Those are later milestones.

BabelDOC remains the only parser, layout model, typesetter, font mapper, and
PDF generator. The project does not create a second layout representation and
does not route production through DOCX or LaTeX.

## Threat model

M0 must remain correct under these ordinary failures:

- the process stops after extraction, after some approvals, or before render;
- BabelDOC assigns new random diagnostic IDs on a later parse;
- a parser setting, page selection, engine version, or input PDF changes;
- identical source strings occur in multiple locations;
- a model drops, duplicates, reorders, or invents formula/style tokens;
- a result set is partial, duplicated, stale, or from another document;
- one target fails reconstruction after other targets have already validated;
- an operator corrects a previously approved target;
- SQLite or an artifact write is interrupted.

M0 does not attempt to protect against a hostile local administrator who can
rewrite the database, executable, and artifact files together.

## Non-negotiable invariants

1. **No random identity input.** Diagnostic IDs may be logged but never enter
   a stable key, revision, manifest digest, or approval lookup.
2. **Prepared artifact first.** The exact IL immediately after
   `StylesAndFormulas` is persisted before external translation. A resumed run
   restores that artifact, rather than treating a fresh random parse as the
   same object graph.
3. **Profile binding.** Source bytes, normalized parser input, BabelDOC build,
   page/part selection, source/target languages, and every segmentation or
   placeholder-affecting option are bound to the prepared snapshot.
4. **Deterministic locators.** A unit locator is the prepared artifact's page
   ordinal plus paragraph ordinal. Repeated text is never used as a locator.
5. **Revision binding.** A unit revision covers its locator, source text,
   protected structure, layout class, and quantized geometry.
6. **Complete classification.** Every prepared paragraph is either a
   translatable unit, a safe exclusion with an explicit reason, or a blocking
   unsupported unit. Human-readable vertical or otherwise unsupported text is
   a blocker, not a silent skip.
7. **Exact protected structure.** Every generated formula/style token occurs
   exactly once in source and target. No reserved-but-undeclared token may
   appear. Style pairs remain ordered, non-empty, and non-crossing.
8. **Whole-document validation.** The provider must return exactly one current
   approval for every translatable unit and no other unit.
9. **Atomic write-back.** All target compositions are reconstructed in a
   staging area. No paragraph is mutated until every result has validated and
   reconstructed successfully.
10. **Append-only approval history.** Approval revisions are immutable, but an
    active pointer may atomically supersede one revision with another. A typo
    correction never requires deleting the project database.
11. **Truthful stop.** Pending, unsupported, stale, corrupt, and incomplete are
    explicit terminal outcomes for a run. None may fall through to PDF output.

## Identity model

All digests are SHA-256 over RFC 8785-style canonical JSON (UTF-8, sorted keys,
no insignificant whitespace). Text is NFC-normalized and line endings are LF
before hashing. Hash inputs carry an explicit schema namespace.

### Project binding

`project_key` binds the user project to:

- original input PDF SHA-256;
- source and target BCP-47 language tags;
- the selected translation profile.

Opening one database with a different project key fails closed.

### Prepared snapshot

`snapshot_key` is derived from:

- `project_key`;
- normalized parser-input PDF SHA-256;
- BabelDOC package version and pinned source commit;
- page range and split-part identity;
- a canonical extraction-profile payload;
- prepared IL artifact SHA-256.

The artifact is stored as a content-addressed file and verified before every
restore. The SQLite transaction records it only after an atomic file rename.

The parser-input digest is not the SHA-256 of PyMuPDF's ordinary save output.
PyMuPDF refreshes a volatile trailer ID on each save, so that value would break
resume for an unchanged source. The digest is computed from an in-memory
canonical save (`garbage=4`, `clean`, `deflate`, `no_new_id`) after replacing
both trailer IDs with a deterministic value derived from the original source
digest and split-part key. The PyMuPDF version is also bound into the extraction
profile. Two full parses of the same source must therefore recover one context,
artifact, snapshot, and unit set.

### Unit locator and revision

The locator is `{page_ordinal, paragraph_ordinal}` within the restored
prepared artifact. It intentionally excludes `paragraph.debug_id`.

`unit_key` is a digest of the contract namespace, snapshot key, and locator.
`unit_revision` additionally covers:

- exact prepared source text;
- placeholder specifications in source order;
- layout label;
- vertical flag;
- bounding box quantized to 1/1000 PDF point.

Unit identity distinguishes repeated strings; unit revision detects structural
or content drift.

## Prepared-document manifest

The document-level provider receives one immutable value containing:

- project, snapshot, engine, profile, and language bindings;
- ordered translatable units;
- ordered safe exclusions with reason codes;
- ordered blockers with reason codes;
- a manifest digest covering all of the above.

Paragraph geometry is optional only for a `MISSING_GEOMETRY` blocker. The
manifest records `null` in that case; it never invents a zero-sized box that
could be mistaken for real layout data.

Initial reason codes include:

| Classification | Reason | Effect |
| --- | --- | --- |
| translatable | `TEXT` | approval required |
| safe exclusion | `EMPTY` | no approval |
| safe exclusion | `PURE_NUMERIC` | no approval |
| safe exclusion | `FORMULA_ONLY` | no approval |
| safe exclusion | `DEBUG_ARTIFACT` | no approval |
| blocker | `VERTICAL_TEXT_UNSUPPORTED` | stop before provider resolution |
| blocker | `UNKNOWN_COMPOSITION` | stop before provider resolution |
| blocker | `MISSING_GEOMETRY` | stop before provider resolution |

Short human-readable strings are translatable. M0 v2 does not inherit
BabelDOC's default minimum-length skip.

## Placeholder codec and contract

The external path uses a BabelDOC-owned codec, separate from any model client.
Tokens include a per-snapshot namespace and kind, for example formula, style
open, and style close. The codec searches the entire source text and chooses a
namespace that has no collision.

Each unit carries ordered placeholder specifications rather than only a token
multiset:

- formula: one exact token;
- rich style: one exact open token and one exact close token;
- a reserved-token recognizer for the chosen namespace.

Validation rejects missing, duplicate, reordered, invented, empty, overlapping,
or crossing structures before calling BabelDOC reconstruction. Literal text
that resembles a different namespace remains ordinary source text.

## Provider protocol

The minimal BabelDOC seam has three responsibilities:

1. offer or restore the prepared IL artifact for the bound document context;
2. receive the complete prepared-document manifest in one call;
3. return approval envelopes for the exact unit set.

An approval envelope contains `approval_id`, `unit_key`, `unit_revision`,
`target_text`, and `target_sha256`. The fork has no model, database, retry, or
terminology logic.

The adapter performs these steps in order:

1. restore a verified prepared artifact when one exists, otherwise persist the
   current post-style/formula IL;
2. classify every paragraph and prepare all translatable units;
3. reject blockers;
4. call the provider once;
5. validate exact coverage and every envelope;
6. reconstruct every target into detached compositions;
7. mutate all paragraphs only after step 6 succeeds;
8. return the translated IL to the unchanged BabelDOC typesetting pipeline.

Exceptions from steps 1–7 propagate out of the translation stage. The
per-paragraph catch-and-continue behavior of BabelDOC's ordinary translator is
not used by the external path.

## State model

SQLite is the authority for metadata and active revisions; large prepared IL
artifacts are content-addressed files beside it. Required logical tables are:

- `project`: one immutable project binding and schema version;
- `prepared_context`: durable context-to-artifact lookup written before the
  manifest, so interruption immediately after extraction remains resumable;
- `prepared_snapshot`: immutable engine/profile/artifact binding;
- `unit`: immutable unit revision and classification;
- `approval_revision`: immutable validated target revisions;
- `active_approval`: one atomically replaceable pointer per unit;
- `event`: append-only state transitions with structured payloads.

Foreign keys, WAL mode, busy timeout, and explicit transactions are mandatory.
Schema creation and migrations are versioned; a newer database is never opened
by older code.

Partial approval revisions may be accumulated. Resolution remains fail-closed
until active coverage is exact for the full translatable set.

## Acceptance matrix

M0 v2 is not complete until automated tests prove all rows below.

| Area | Required proof |
| --- | --- |
| identity | two extractions with different random debug IDs yield the same locator/unit keys when restoring the same prepared artifact |
| identity | repeated identical strings have different unit keys |
| binding | input, engine, profile, language, page-part, or artifact drift is rejected |
| coverage | missing, extra, and duplicate approvals are rejected |
| staleness | wrong unit revision or target digest is rejected |
| placeholders | loss, duplication, invention, reorder, empty pair, overlap, and crossing are rejected |
| exclusions | every paragraph has exactly one classification |
| blockers | meaningful vertical and unknown-composition text stop the run |
| atomicity | failure reconstructing the final unit leaves every paragraph unchanged |
| revisions | a corrected approval supersedes the active pointer while preserving history |
| recovery | interruption after snapshot and after partial approvals resumes without changing completed work |
| persistence | prepared artifact temp-write failure cannot create a committed database reference |
| compatibility | old v1 adversarial tests still pass where their invariants remain valid |
| integration | a synthetic PDF completes prepare → approve → restore → render through BabelDOC |
| regression | rich-text font/style and formula objects survive the external round trip |

The final two rows require real BabelDOC execution, not mocked IL classes.
