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

## M0 v2 checkpoint status

Implemented in this milestone:

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

The provider branch is rebased on BabelDOC `v0.6.4` (`17480db`) and has a
regression test proving that rich-text styling and formula placeholders survive
when BabelDOC's built-in LLM translator is not used.

Not yet implemented:

- model calls, terminology, review, adjudication, and PDF postflight;
- a broader corpus of licensed/synthetic layout fixtures and visual QA gates;
- the one-command production `pubtrans translate` workflow;
- the thin `translate-publication-pdf` Skill.

This is not yet an end-user translator. M0 establishes the data and write-back
contract on which the translation and review kernel will be built.

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
[architecture](docs/architecture.md), and
[research notes](docs/research-notes.md). The target runtime is Python 3.10
through 3.13.
