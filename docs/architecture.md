# Architecture

Status: M0 alpha, high-depth audit revision  
Baseline: BabelDOC 0.6.4 (`17480db9df92ddcb37349ce34b312335226e8ec9`)

## Product boundary

The product accepts a complete source PDF and ultimately emits a verified
Simplified Chinese PDF. BabelDOC is the only PDF parsing, intermediate-layout,
typesetting, and PDF-generation engine. The project does not generate LaTeX or
DOCX and does not maintain a parallel layout model.

The core is a standalone Python application. A later Codex Skill will be a thin
launcher and monitor; it will not split, translate, merge, or persist books in
the chat context.

## Staged internally, one command externally

Implementation and recovery are staged because parsing, terminology,
translation, independent review, adjudication, typesetting, and final-PDF QA
have different invariants and failure modes. Each stage commits durable state
and passes an explicit quality gate.

The stages are not intended as manual user operations. The public entry point
will be:

```bash
pubtrans translate source.pdf --target zh-Hans --profile publication
```

That command will create or resume a project and continue until `RELEASED`, or
stop with a structured, truthful blocker. Approved work is never repeated
merely because the process was interrupted.

## Stable boundaries

1. **BabelDOC adapter** prepares document-level translation units after rich
   text and formula processing, and writes approved text back into the same IL.
2. **Translation kernel** owns context, terminology by sense, candidates,
   independent review, adjudication, Chinese editing, and approval.
3. **State store** is the sole source of run state. SQLite transactions and an
   append-only event trail replace chat progress claims and ad-hoc CSV ledgers.
4. **Quality gates** prevent incomplete, stale, structurally damaged, or
   visually invalid output from being called a final translation.

## BabelDOC fork policy

The fork contains only a document-level provider seam and its contract tests.
It does not change BabelDOC layout algorithms. The provider receives all
prepared units together and must return an exact approved map before any IL is
mutated.

The current contract rejects:

- missing, extra, or duplicate units;
- source or structural signatures from an earlier extraction;
- empty approved text;
- lost, added, or duplicated formula/style placeholders;
- reversed, empty, overlapping, or crossing rich-text placeholder pairs;
- vertical text that the external path cannot safely support.

The fork is pinned to an upstream commit and periodically rebased. Once the
interface is mature, it should be proposed upstream and the fork dependency
removed if accepted.

## Delivery stages

| Milestone | Exit condition |
| --- | --- |
| M0: contract | Stable units, immutable approval map, rich-text-safe BabelDOC write-back |
| M1: kernel | Candidate generation, terminology, context packages, review and adjudication state |
| M2: recovery | Leases, retries, budgets, precise invalidation and crash recovery |
| M3: PDF loop | Real fixture PDF from extraction through approved write-back and rendering |
| M4: verification | Text coverage, images, fonts, clipping, overlap and visual regression gates |
| M5: product | One-command CLI, packaging, thin Skill and two distinct full-book trials |

Publishing intermediate milestones does not make them production releases.
The old translation Skill is retired only after M5's acceptance criteria pass.

## Public repository boundary

Only code, schemas, documentation, and licensed or synthetic fixtures belong in
Git. Source books, translated books, API keys, SQLite run databases, raw model
responses, and copyrighted regression PDFs must remain outside the repository.
