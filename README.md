# Publication PDF Translator

> [!WARNING]
> The M0 v1 checkpoint in this repository's history is invalidated and must not
> be used for translation work. Its unit identity included BabelDOC's randomly generated
> paragraph `debug_id`, so a fresh extraction could not resume the same unit set.
> M0 v2 is a clean-room replacement built around persisted prepared IL and
> deterministic locators; it does not migrate or reuse v1 state databases.

`publication-pdf-translator` is an experimental, resumable, fail-closed
translation runtime for producing layout-restored translated PDFs with
BabelDOC.

The software project owns document identity, stable translation units,
terminology, candidate/review/approval state, recovery, and final-PDF quality
gates. BabelDOC remains the only layout and PDF-generation engine. A future
Codex Skill will only invoke and monitor this runtime.

The internal implementation is checkpointed in stages, while the intended user
experience is one command for a complete book. A future `pubtrans translate`
command will create or resume a project and continue until a verified PDF or a
real blocking condition is reached.

## M4 checkpoint status

The M0 v2 preparation/write-back contract, M1 semantic quality kernel, M2
remote-call recovery controller, and M3 real BabelDOC loop remain intact. M4
now also implements:

- stable project, prepared-snapshot, paragraph, and translation-unit identities
  that exclude BabelDOC diagnostic IDs;
- engine, source, split-part, extraction-profile, and canonical parser-input
  bindings;
- complete paragraph coverage: every paragraph is a translation unit, a proven
  safe exclusion, or an explicit blocker;
- source and placeholder signatures;
- paired rich-text placeholder validation, including order, non-empty spans,
  and non-overlap;
- exact approved-map validation;
- transactional SQLite storage with immutable snapshots, append-only approval
  revisions, and atomically replaceable active-approval pointers;
- content-addressed prepared-IL artifacts and a durable context-to-artifact
  mapping that survives interruption before manifest registration;
- a provider that refuses missing, extra, stale, duplicate, or structurally
  damaged translations;
- a BabelDOC adapter that captures the complete extracted unit set on its first
  pass and supplies only the exact persisted approval map on a later pass;
- adversarial unit and contract tests for identity, recovery, coverage,
  revisions, split parts, and placeholder corruption;
- a synthetic-PDF integration test that completes prepare → approve → restore →
  BabelDOC render, then repeats the full run against the same persisted
  artifact and unit set;
- a minimal BabelDOC provider patch maintained in the sibling working copy.
- risk-adaptive one-to-three isolated translation lanes instead of fixed
  candidate theater;
- concept-oriented terminology decisions with authority/domain/corpus evidence,
  counterevidence, conventionality, independent approval and safe source
  retention when unresolved;
- exact source-occurrence and target-application maps for every governed term;
- deterministic source-only context packages;
- blind bilingual review, explicit adjudication, conservative Chinese editing,
  edit-impact verification and whole-document consistency review;
- additive schema-3 persistence, immutable stage slots and crash-safe resume;
- atomic activation of a complete verified release into M0 approvals;
- content-addressed provider-call responses that close the paid-call crash
  window;
- fenced expiring leases and stale-worker rejection;
- explicit transient/permanent retry classification and bounded backoff;
- immutable call/token/cost budgets reserved before invocation;
- dependency-aware reuse of unchanged translations after downstream replans;
- sanitized failure ledgers and schema-4 additive migration;
- a two-pass application loop from BabelDOC prepared-IL capture through the
  M1/M2 release and restored-PDF rendering;
- exact post-render release/approval reconciliation;
- completed-project reuse without duplicate service calls or a redundant
  second render;
- a real two-page CJK/font/formula/terminology fixture through the actual
  BabelDOC high-level pipeline;
- source-bound reopening and rasterization of the actual final PDF;
- exact page-count, page-geometry, blank-page and visible-ink gates;
- per-page approved-translation and protected number/URL/formula coverage;
- decoded-image digest preservation, font/`.notdef`/U+FFFD checks, text-boundary
  and material-overlap checks;
- immutable schema-5 reports and atomic activation of only a passing final PDF;
- a dedicated content-addressed `.pdf` store that re-verifies active artifacts;
- adversarial proof for missing pages, text, formulas and images, corrupt PDFs,
  and post-activation file mutation;
- 76 passing core, adversarial, recovery and real-PDF BabelDOC tests.

The provider branch is rebased on BabelDOC `v0.6.4` (`17480db`) and has a
regression test proving that rich-text styling and formula placeholders survive
when BabelDOC's built-in LLM translator is not used.

Not yet implemented:

- production model/provider calls and automated terminology retrieval;
- a broader corpus of licensed/synthetic layout fixtures;
- the one-command production `pubtrans translate` workflow;
- the thin `translate-publication-pdf` Skill.

This is not yet an end-user translator. M4 establishes a verified final-PDF
release boundary; M5 adds production providers, automated terminology
research, two-domain trials, the thin Skill and the one-command product.

## Development

Run the M0 v2 core, adapter, and full synthetic-PDF regression suite with:

```bash
python -m pip install -e '.[babeldoc,dev]'
python -m pytest -q
```

The cross-repository adapter tests use the audited provider branch from the
[project fork](https://github.com/Jace-Planeswalker/BabelDOC/tree/feature/document-translation-provider-v2),
pinned to commit `0b3b03ab1ed29c15245dca49f6cb3afece046f95`:

```bash
python -m pytest -q tests/test_babeldoc_adapter.py \
  tests/test_babeldoc_pipeline_v2.py
```

Stock BabelDOC is not substituted silently: the optional dependency points to
the exact fork commit containing the document-level provider contract.

See [the M0 v2 specification](docs/m0-v2-spec.md),
[the M1 quality-kernel specification](docs/m1-kernel-spec.md),
[the M2 recovery-controller specification](docs/m2-recovery-spec.md),
[the M3 PDF-loop specification](docs/m3-pdf-loop-spec.md),
[the M4 artifact-gate specification](docs/m4-artifact-gates-spec.md),
[architecture](docs/architecture.md), and
[research notes](docs/research-notes.md). The target runtime is Python 3.10
through 3.13.
