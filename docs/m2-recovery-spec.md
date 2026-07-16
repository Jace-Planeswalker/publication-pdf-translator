# M2 recovery-controller specification

Status: implemented checkpoint

Depends on: M1 quality kernel

Database schema: 4

## Purpose

M1 can resume after a semantic stage is persisted. M2 closes the more expensive
gap: a provider may have returned and charged successfully just before the M1
candidate/review/edit object is committed. M2 persists the typed provider
response first, so replay decodes that response instead of paying for the same
call again.

The recovery controller is a wrapper around the M1 service protocols. It does
not change review semantics, accept partial output, or turn retries into an
excuse to ignore deterministic contract failures.

## Content-addressed calls

Every remote call has a descriptor containing:

- stage (`GENERATION`, `REVIEW`, `ADJUDICATION`, `EDIT`, `VERIFICATION`, or
  `GLOBAL_REVIEW`);
- a canonical dependency payload and digest;
- a human-readable, non-authoritative slot hint.

The call key is derived from stage and dependency digest. Successful response
JSON is immutable and globally reusable. A cache hit consumes no call, token,
or cost budget.

Generation dependencies deliberately exclude plan-derived IDs while retaining
the exact source unit/revision, neighboring source records, source brief,
languages, terminology dossier, lane and actor profile. Therefore changing a
downstream editor or verifier creates a new M1 plan but does not retranslate an
unchanged unit. Source, context, terminology, translator, model or prompt drift
does create a new generation call. Later stages currently fingerprint their
complete typed requests because their outputs contain plan-local opaque
references.

This is dependency invalidation, not manual cache deletion. Cached data that no
longer matches cannot be selected by the new call key.

## Lease and fencing contract

One live worker may own a call. Lease rows contain owner, random 256-bit token,
and timezone-aware expiry. Acquisition uses `BEGIN IMMEDIATE` so two workers
cannot both reserve the same call.

When a lease expires:

1. its running attempt is marked `ABANDONED`;
2. the call returns to `PENDING`;
3. a new worker receives a different token.

Every success or failure commit rechecks owner, token and expiry. The old
worker is fenced: a late response cannot overwrite the new attempt or a cached
success. Lease TTL is configurable and must exceed the provider adapter's
maximum blocking interval; long-running adapters may add heartbeat renewal at
the scheduler boundary without changing stored identities.

## Attempts and retry classification

Every paid attempt is appended before the provider is invoked. Its ledger
records scope, ordinal, lease token, conservative usage estimate, start/end,
outcome and a short sanitized error.

Retryable by default:

- explicit transient provider errors;
- rate limits (respecting bounded `retry_after`);
- timeouts, connection failures and OS-level transport failures.

Not retryable by default:

- M0/M1 contract violations;
- explicit permanent provider errors;
- unknown exceptions.

Backoff is bounded exponential delay. The final transient failure becomes
`EXHAUSTED`; a permanent failure becomes `FAILED_PERMANENT`. Replaying an
identical known-permanent or exhausted call does not invoke the provider. A
corrected model/configuration must produce a changed actor/config revision and
therefore a new dependency key.

M2 stores no traceback or request secret. Error messages are capped and redact
secret-like assignments, bearer credentials and `sk-` tokens before SQLite
persistence.

## Immutable budgets

A plan scope registers one immutable policy with limits for:

- attempted calls;
- conservatively estimated tokens;
- estimated micro-US-dollars.

The next attempt reserves all three counters atomically before remote
invocation. Retries consume budget because providers may charge failed or
interrupted attempts. A call that would exceed any limit is rejected before
the provider runs and releases its lease. Cached successes are free.

M2 intentionally uses estimates at this boundary: a conservative reservation
is safer than relying on provider usage metadata that may be absent after a
transport failure. Production adapters can set stage/model-specific estimates.

## Schema and states

Schema 4 adds only `m2_*` tables for budgets, calls, leases, attempts and
events. Schema 2 migrates through 3; schemas 2, 3 and 4 remain readable by the
lower milestone stores without rewriting earlier payloads.

Call states are `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED_PERMANENT` and
`EXHAUSTED`. Attempt outcomes additionally distinguish retryable failure,
permanent failure and abandoned work.

## Implemented proof

Tests prove cached replay, three-attempt transient recovery, permanent failure
suppression, pre-call budget enforcement, expired-lease takeover, stale-worker
fencing, secret redaction, schema-3-to-4 migration, all-stage kernel wrapping,
and reuse of unchanged translations after a downstream-only replan.

Together with M0, M1 and the BabelDOC synthetic-PDF regression, 72 tests pass.
