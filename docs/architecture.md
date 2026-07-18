# Architecture

Status: M6 engineered research product implemented and verified end to end

Baseline: BabelDOC 0.6.4 (`17480db9df92ddcb37349ce34b312335226e8ec9`)

## Product boundary

The product accepts a complete source PDF and emits a verified
Simplified Chinese PDF. BabelDOC is the only PDF parsing, intermediate-layout,
typesetting, and PDF-generation engine. The project does not generate LaTeX or
DOCX and does not maintain a parallel layout model.

The core is a standalone Python application. The Codex plugin is a control
plane with a dependency-free adapter, local MCP server and orchestration Skill.
It installs a pinned application release, binds operator inputs, starts and
monitors durable jobs, and collects only verified output. It does not split,
translate, merge, or persist books in the chat context.

## Staged internally, one command externally

Implementation and recovery are staged because parsing, terminology,
translation, independent review, adjudication, typesetting, and final-PDF QA
have different invariants and failure modes. Each stage commits durable state
and passes an explicit quality gate.

The stages are not intended as manual user operations. The controlled public
entry points are:

```bash
pubtrans init /absolute/source.pdf --project /absolute/project --config config.json
pubtrans doctor /absolute/project
pubtrans run /absolute/project
pubtrans status /absolute/project
pubtrans collect /absolute/project --destination /absolute/delivery
```

The MCP adapter exposes the same state machine as bootstrap, init, doctor,
start, poll, status and collect tools. `run` resumes until `RELEASED` or a
structured truthful blocker. Approved work is never repeated merely because
the process or chat was interrupted.

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
| M0: contract | Stable prepared-document units, append-only approval revisions, rich-text-safe BabelDOC write-back |
| M1: kernel | Risk-adaptive isolated candidates, evidence-backed terminology, source context, sequential review, adjudication, Chinese editing, unit and document verification, and atomic release |
| M2: recovery | Content-addressed service cache, fenced leases, classified retries, immutable budgets, precise generation invalidation and crash recovery |
| M3: PDF loop | Real two-page CJK fixture from BabelDOC extraction through M1/M2 release, persisted-IL write-back, rendering, and zero-call resume |
| M4: verification | Actual final-PDF reopening/rasterization, approved-text and protected-anchor coverage, images, fonts, clipping, overlap, immutable reports and passing-artifact activation |
| M5: product | One-command CLI, production Responses provider, evidence-governed terminology, bounded whole-book review, thin Skill/plugin and two distinct actual-PDF trials |
| M6: operator | Content-bound project initialization, read-only preflight/status, pinned runtime bootstrap, durable background jobs, local MCP tools, canonical orchestration Skill and verified delivery collection |

Publishing intermediate milestones does not make this a commercial production
release. M6 is a complete research product: the old monolithic prompt workflow
is replaced by an executable control plane whose Skill only orchestrates
application-owned state and gates.

## M6 control plane

`control/project.json` immutably binds the source PDF, secret-free product
configuration, optional terminology evidence, runtime compatibility and layout
options. `status` and `doctor` are read-only and never create a database simply
because an operator inspected a path. Mutation of a bound input fails closed.

The plugin bootstrap installs the immutable `release/v0.3.0` ref into a
versioned virtual environment outside Git. The stdio MCP server has no third-party
dependencies; all translation dependencies live in that isolated runtime.
Background jobs write small control records and stdout/stderr paths beneath
`control/jobs/`, so another MCP process can poll or resume without a chat-owned
process handle.

Collection is a separate gate. It accepts only `RELEASED`, revalidates the
published PDF against the active content-addressed artifact, refuses to
overwrite different bytes, and emits a delivery manifest covering the PDF and
verification report.

## M5 runtime topology

Planning calls are chunked and independently cached. Terminology citations are
only discovery until the cited page is fetched and the candidate is found in
the captured text. Independent review establishes stance, exact sense and
domain; code derives confidence and groups HTTP evidence by publishing
authority so multiple pages cannot inflate corroboration.

Unit translation proceeds sequentially through the M1 quality bus. The final
whole-publication review is itself chunked: local reports emit compact
continuity observations, then a separate synthesis compares distant chunks.
Any local blocker survives synthesis. Each remote call has its own immutable
M2 slot, so a crash between chunks does not repeat completed paid work.

After BabelDOC restoration, M4 verifies and content-addresses the actual PDF.
M5 atomically publishes that byte sequence with a JSON report. Status checks
both the active content-addressed artifact and the published copy.

## Public repository boundary

Only code, schemas, documentation, and licensed or synthetic fixtures belong in
Git. Source books, translated books, API keys, SQLite run databases, raw model
responses, and copyrighted regression PDFs must remain outside the repository.
