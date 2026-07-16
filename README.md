# Publication PDF Translator

> [!WARNING]
> The published M0 v1 checkpoint is invalidated and must not be used for
> translation work. Its unit identity included BabelDOC's randomly generated
> paragraph `debug_id`, so a fresh extraction could not resume the same unit
> set. A clean-room M0 v2 is replacing that design with a persisted prepared-IL
> snapshot and deterministic locators.

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

## M0 alpha status

Implemented in this milestone:

- stable translation-unit identities;
- source and placeholder signatures;
- paired rich-text placeholder validation, including order, non-empty spans,
  and non-overlap;
- exact approved-map validation;
- transactional SQLite storage with immutable unit snapshots and approvals;
- a provider that refuses missing, extra, stale, duplicate, or structurally
  damaged translations;
- a BabelDOC adapter that captures the complete extracted unit set on its first
  pass and supplies only the exact persisted approval map on a later pass;
- unit tests for the fail-closed contract;
- a minimal BabelDOC provider patch maintained in the sibling working copy.

The provider branch is rebased on BabelDOC `v0.6.4` (`17480db`) and has a
regression test proving that rich-text styling survives even when BabelDOC's
built-in LLM translator is not used.

Not yet implemented:

- an end-to-end fixture PDF rendered through BabelDOC;
- model calls, terminology, review, adjudication, and PDF postflight;
- the thin `translate-publication-pdf` Skill.

This is not yet an end-user translator. M0 establishes the data and write-back
contract on which the translation and review kernel will be built.

## Development

The M0 core uses only the Python standard library:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_contract.py' -v
```

The cross-repository adapter tests use the audited provider branch from the
[project fork](https://github.com/Jace-Planeswalker/BabelDOC/tree/feature/document-translation-provider),
pinned to commit `9428e54d6b78415b194b8058ba984cc95ef6d5db`:

```bash
python -m pip install -e '.[babeldoc,dev]'
python -m pytest tests/test_babeldoc_adapter.py
```

Stock BabelDOC is not substituted silently: the optional dependency points to
the exact fork commit containing the document-level provider contract.

See [the architecture](docs/architecture.md) and
[research notes](docs/research-notes.md). The target runtime is Python 3.10
through 3.13.
