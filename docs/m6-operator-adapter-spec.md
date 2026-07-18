# M6 operator-adapter specification

Status: implemented research release

## Decision

The product remains the standalone `publication-pdf-translator` application.
It is not reimplemented as a Skill and it does not fork every project whose
ideas it adopts. The Codex integration is a plugin containing:

1. a canonical orchestration Skill;
2. a dependency-free Python adapter;
3. a local stdio MCP server exposing bounded operator tools.

This split keeps translation, terminology, recovery, BabelDOC layout and final
artifact truth in testable application code. The Skill supplies decision rules
and blocker routing. The adapter supplies process/runtime mechanics. MCP makes
those mechanics callable without turning chat text into an execution protocol.

## Release and dependency contract

The plugin version and application compatibility are `0.3.0`. Bootstrap
installs the immutable Git branch `release/v0.3.0` with the `babeldoc` extra
into:

```text
<runtime-home>/versions/0.3.0/
```

It validates `pubtrans.__version__` in an isolated Python process before reuse.
An incompatible existing version directory is never silently repaired or
overwritten. Installation is staged and atomically renamed; an exact temporary
directory created by the current attempt is the only directory removed after a
failed install. Runtime homes inside an unignored Git worktree are blocked.
The release branch is created once from the verified merge commit and is never
moved; routine development continues on `main`.

The MCP server itself imports only the Python standard library. Translation
dependencies and BabelDOC are isolated in the pinned application environment.

## Content-bound project contract

`pubtrans init` validates a readable, non-empty, unencrypted PDF and creates:

```text
<project>/
  control/project.json
  inputs/source.pdf
  inputs/config.json
  inputs/evidence.json        # optional
```

The control manifest binds SHA-256 digests, source size/name, application
major/minor compatibility, scan-detection policy and primary font. Initialization
is idempotent only when every bound property matches. Existing unmanaged files,
unignored Git locations and later input mutation fail closed.

`run` and `resume` take only a project path. They reconstruct all runtime
arguments from the bound files; an agent cannot accidentally resume a database
against a different source, config or evidence dossier.

## Operator state machine

| Product state | Meaning | Allowed next operation |
| --- | --- | --- |
| `UNINITIALIZED` | No content-bound project | `init` |
| `INITIALIZED` / `NEW` | Bound input, no completed work | `doctor`, then `start` |
| `IN_PROGRESS` | Durable partial work | `start`/`resume`, then `poll` |
| `VERIFYING` | Release candidate exists but final gate is unfinished | Continue/poll |
| `RELEASED` | Active report and published PDF bytes revalidate | `collect` |
| `BLOCKED` | Integrity, environment or quality invariant failed | Repair cause, then resume |

`status` does not initialize state. For a released project it reopens the active
artifact record and compares the published PDF digest. `doctor` composes Python
compatibility, project/source integrity, Git safety, credential presence,
BabelDOC provider seam, font resolution and free-space checks. It never returns
credential values.

## MCP tools

The plugin declares one stdio server in `.mcp.json` and exposes:

- `pubtrans_bootstrap`
- `pubtrans_init`
- `pubtrans_doctor`
- `pubtrans_start`
- `pubtrans_poll`
- `pubtrans_status`
- `pubtrans_collect`

All filesystem arguments must be absolute. Bootstrap, init, start and collect
are idempotent writes; doctor, poll and status are read-only. API keys are not
tool schema fields and are inherited only through named environment variables.

The repo marketplace at `.agents/plugins/marketplace.json` points to the plugin
bundle, allowing Codex to add the GitHub repository as a marketplace and install
the plugin without copying directories or editing configuration by hand.

`start` repeats doctor checks, holds an exclusive launch lock and refuses to
duplicate a live job. A small detached runner writes JSON task state plus
stdout/stderr log paths under `<project>/control/jobs/`. `poll` combines that
task record with a fresh application status, so a restarted MCP process can
continue monitoring and released bytes are never inferred from process exit
alone.

## Verified collection

`collect` accepts only `RELEASED`. It copies the single verified PDF and
verification report, then creates `delivery-manifest.json` with project/source
identity, runtime version, file sizes and SHA-256 digests. Repeating collection
with identical bytes succeeds. A destination file with different bytes is not
overwritten.

The main deliverable is still a restored-layout PDF. M6 adds no LaTeX or DOCX
pipeline.

## Acceptance evidence

M6 tests cover:

- read-only status of an uninitialized path;
- idempotent initialization and bound-input mutation;
- unignored/ignored Git project locations;
- secret-free doctor blockers;
- run/resume argument reconstruction from bound inputs;
- released-only idempotent collection and collision refusal;
- pinned runtime discovery and absolute-path enforcement;
- detached start/poll continuity using a clean fake runtime;
- isolated stdio MCP initialization, tool discovery and tool invocation;
- plugin manifest, MCP declaration, canonical Skill and metadata contracts;
- all prior M0–M5 unit, recovery, BabelDOC and real-PDF product trials.
