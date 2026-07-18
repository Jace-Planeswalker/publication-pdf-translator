# M3 BabelDOC PDF-loop specification

Status: implemented checkpoint

Depends on: M0 provider seam, M1 quality kernel, M2 recovery controller

## Purpose

M0 proved that BabelDOC could capture a prepared document and later consume a
manually supplied exact approval map. M3 removes that manual gap. One
application loop now drives:

1. source PDF parsing and prepared-IL capture by BabelDOC;
2. M0 manifest persistence;
3. terminology and kernel-plan construction;
4. the complete M1 bus through a store-aware M2 service factory;
5. atomic release activation;
6. BabelDOC restoration from the persisted IL and final PDF generation;
7. post-render confirmation that active approvals and release did not change.

There is still one layout engine and one layout model: BabelDOC. M3 introduces
neither LaTeX nor DOCX and does not reconstruct the PDF independently.

## Two-pass provider protocol

The provider seam intentionally fails closed during a new project. BabelDOC's
first invocation saves content-addressed prepared XML, publishes the complete
paragraph/unit manifest, then raises `ApprovalSetError` because no complete
approval set exists. The M3 loop catches only that expected condition. Parsing,
artifact, blocker and contract errors propagate unchanged.

The loop opens the same database as a schema-4 `RecoveryStore`, verifies the
prepared artifact, loads the exact document, asks the planner for one validated
terminology snapshot and kernel plan, creates plan-scoped resilient services,
and runs `TranslationKernel` to a complete active release.

A second BabelDOC invocation loads the saved IL instead of re-extracting an
independent unit set. The provider resolves only the M0 approvals atomically
activated by that release and writes them back before BabelDOC typesets the
translated PDF.

After rendering, the loop reopens the store and proves:

- resolved M0 approvals exactly equal the release approvals;
- the M1 active release still equals the one used for the run;
- the prepared artifact remains hash-valid.

## Resume behavior

On a completed project, the initial BabelDOC invocation can already render. The
loop still rebuilds the deterministic plan and replays the kernel contracts. If
the previously active release equals the resulting release, that already
rendered artifact is returned and no second render occurs. M1 stages and M2
provider responses are cache hits, so no model call repeats.

If planning changes the release, the first artifact is known to contain the old
approval set and is discarded; the loop runs the final render with the new
release. This prevents a superficially successful stale PDF from being
returned after a prompt, terminology or actor change.

## Implemented real-PDF proof

The M3 integration fixture creates a real two-page input PDF and executes the
actual BabelDOC high-level pipeline. The extracted document has four units,
including mathematical protected content. Its deterministic research planner
builds an evidence-backed `world → 世界` concept with first-use source
retention, and assigns the formula-bearing unit to an R3 two-lane route.

Every unit passes translation, blind review, adjudication, Chinese edit,
verification and whole-document review through M2. BabelDOC then renders with
a real Source Han CJK font. The test reopens the PDF, confirms two pages, and
extracts both translated text and the protected `E = mc2` expression. A second
complete run produces the same release with zero semantic service calls.

The fixture services are deterministic test doubles: M3 proves application
wiring and restored-PDF provenance, not production model quality. Real
provider adapters and independent book trials are M5; artifact-quality gates
are M4.

Together with earlier milestones, 73 tests pass.
