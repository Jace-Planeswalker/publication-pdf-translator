# M4 final-PDF artifact-gate specification

Status: implemented checkpoint

Depends on: M0 prepared-document contract, M1 semantic release, M2 durable
execution, M3 BabelDOC PDF loop

Database schema: 5

## Purpose

M1 proves that approved target strings satisfy the semantic workflow. M3 proves
that BabelDOC can restore those strings into its persisted intermediate layout
and emit a PDF. Neither claim proves that the emitted file is the intended
deliverable. A renderer can still produce a truncated, blank, clipped,
font-damaged, image-damaged or incomplete artifact after semantic approval.

M4 therefore treats the actual bytes written by BabelDOC as an untrusted build
artifact. The verifier reopens and rasterizes that file, compares it with the
bound source PDF and approved release, and issues an immutable `PASS` or
`BLOCK` report. Only a passing report can atomically activate a final PDF.

## Binding contract

Verification fails closed before inspection when:

- the source or target file is absent;
- the source SHA-256 differs from the M0 project binding;
- the release project, snapshot or manifest differs from the prepared document;
- the prepared manifest page count differs from the source PDF.

Every report identity binds the release, project, source and target digests,
verification profile, page counts, canonical findings and metrics. A target
PDF cannot be substituted after the report is created.

## Required artifact gates

The default profile checks:

1. target parsing and rasterization;
2. exact source/target page count;
3. per-page dimensions within a one-point tolerance;
4. visible source pages do not become blank;
5. absolute and source-relative rendered-ink density;
6. every approved substantive target fragment on its original page;
7. source URLs, standalone numbers and equation-like anchors on the same page;
8. source image instances by decoded-image digest;
9. target font resources, `.notdef` references and extracted U+FFFD glyphs;
10. extracted line boxes outside page bounds;
11. material overlap between distinct text blocks.

Whitespace is normalized for target-fragment and anchor matching because PDF
font encodings often extract ordinary spaces as non-breaking spaces. BabelDOC
style and formula placeholders are excluded from literal text matching; their
visible source values are checked separately as protected anchors.

An `ERROR` or `BLOCKING` finding produces `BLOCK`. Warnings are recorded but do
not independently prevent activation. This distinction is part of the profile
identity and cannot be changed retroactively for an existing report.

## Persistence and release authority

Schema 5 adds immutable artifact reports, one atomic active-artifact pointer
and an append-only M4 event trail. Final PDFs use their own content-addressed
store with `.pdf` object names; they are not mixed with prepared BabelDOC XML.
Writes use a flushed, fsynced temporary file and same-directory rename.

Recording verifies the target bytes against the report digest and the exact
registered M1 release. A blocked report may exist as diagnostic data only when
explicitly recorded without activation. The normal activation path rejects it
and rolls back its database row. Loading the active artifact re-verifies file
size and SHA-256, so later local corruption cannot be mistaken for a release.

## Implemented proof

The real M3 fixture now contains two pages, CJK output, a protected formula,
first-use terminology and an embedded source image. Its actual BabelDOC output
passes all gates and is activated in the schema-5 store. Adversarial derivatives
prove that the verifier blocks:

- deletion of a page;
- redaction of approved Chinese text;
- redaction of `E = mc2`;
- replacement of the source image;
- an unparsable target;
- mutation of an already activated content-addressed PDF.

Model tests also prove deterministic identities, payload round trips, severity
semantics and tamper rejection.

## Deliberate limits

M4 supplies deterministic structural and rendered-artifact checks. It does not
claim that pixel equality is desirable after translation, infer whether every
line break is aesthetically optimal, or replace human inspection of unusual
layouts. M5 adds a broader two-domain fixture corpus and product orchestration;
future optional visual models may add evidence, but no probabilistic score may
override exact completeness or protected-content failures.
