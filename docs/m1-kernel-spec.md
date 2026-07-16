# M1 quality-kernel specification

Status: implemented checkpoint

Depends on: M0 v2 prepared-document contract

Database schema: 3

## Decision

M1 is a sequential publication-translation quality bus, not a fixed
"three agents vote" workflow. Every unit follows analysis/context → translation
→ blind bilingual review → adjudication → conservative Chinese editing →
independent verification. Only riskier units receive extra isolated
translations. A final whole-document review checks consistency and discourse
before release.

This design adopts AIDAterm's strongest empirical lesson—terminology-aware
specialized stages outperform undifferentiated generate-and-select—while adding
the controls a PDF publication needs: immutable source identities, exact
placeholder and terminology spans, independent evidence, recovery, and atomic
write-back.

M1 does not parse or typeset PDFs, call a particular vendor API, retry failed
remote calls, or certify rendered-page geometry. BabelDOC remains the only
layout/PDF engine. Retries and invalidation are M2; real PDF fixtures are M3;
rendered-artifact gates are M4; providers and the one-command product are M5.

## Quality topology

Each unit has an immutable risk route:

| Risk | Candidate policy | Typical triggers |
| --- | --- | --- |
| R1 | exactly one strong baseline | ordinary prose with resolved terms and clear syntax |
| R2 | one or more, chosen explicitly | mild ambiguity, dense reference, style sensitivity |
| R3 | at least two isolated candidates | unresolved ambiguity, logic/negation risk, high-impact terminology, tables or unusually compressed prose |

A plan exposes one to three available lanes. R1 never pays for performative
redundancy; R3 cannot silently collapse to one candidate. Lane actor profiles
must be distinct. Candidates are created without seeing peer output.

All routes converge on one ordered bus:

1. the bilingual reviewer receives opaque options and source-only context;
2. the adjudicator either selects an option byte-for-byte or declares a new
   synthesis and resolves every serious finding;
3. the Chinese editor makes a conservative publication-language pass;
4. an independent verifier compares source, adjudication and edit, and rates
   whether the edit improves, preserves, or degrades the result;
5. a global reviewer sees the complete source/target sequence and checks
   cross-unit terminology, reference, register and discourse consistency.

`PASS` is structurally impossible when a serious finding or a degrading edit
remains. A global block prevents release even when every unit passed locally.

## Source and discourse context

Context packages are deterministic and source-only. They contain the current
M0 unit, bounded preceding and following paragraph records (including safe
exclusions such as numbers or formulas), layout/classification metadata, an
optional source-only document brief, and the applicable terminology dossier.

No previous target, chain-of-thought, candidate, review, edit, provider, or
lane identity enters candidate context. Nearest records consume the character
budget first. Package identity changes when any source record, policy,
directive or plan changes.

This is the production-safe subset of the discourse-memory ideas evaluated in
GRAFT: source graph locality and document memory are useful; its research
runtime, naive splitting and dependency stack are not adopted.

## Terminology is an evidence decision, not a glossary guess

One term record represents a concept: project, source expression, explicit
sense, definition and domain. It contains competing Chinese candidates rather
than overwriting alternatives. Each candidate records semantic fit,
conventionality, supporting evidence and counterevidence.

Evidence is auditable: source identity, URI, title, dated excerpt, source type,
tier, stance, sense match and domain match. Confidence gates are deterministic:

- `VERIFIED` requires at least two independent sources, at least one authority
  or domain-primary source, no authoritative contradiction, and an established
  or attested form;
- `SUPPORTED` requires strong or independently corroborated evidence and no
  authoritative contradiction;
- `PROVISIONAL` still requires real attestation and must expose the source form
  on first use;
- `RETAINED_UNRESOLVED` keeps the source expression instead of inventing a
  Chinese term.

Selecting a rarer confirmed form while a more conventional confirmed form
exists requires an explicit accuracy-based override. This directly targets the
observed failure mode where web search finds an obscure but technically
possible equivalent and mistakes it for the mainstream Chinese term.

The intended research order is authoritative Chinese termbanks (for example
Termonline/CNTERM where applicable), official naming, domain-primary Chinese
sources, established parallel publications, and corpus attestations. Search
snippets and general references cannot by themselves produce `VERIFIED`.

## Occurrence and rendering proof

A decision is bound to exact source spans on exact M0 unit revisions. The
snapshot rejects stale, unknown, overlapping or placeholder-crossing
occurrences and determines first use in document order.

Four treatments remain distinct:

| Treatment | Required target rendering |
| --- | --- |
| `TRANSLATE_ONLY` | approved Chinese term |
| `TRANSLATE_WITH_SOURCE_FIRST` | `中文（source）` at first bound use, then Chinese |
| `TRANSLATE_WITH_SOURCE_ALWAYS` | `中文（source）` at every bound use |
| `RETAIN_SOURCE` | source expression unchanged |

Every candidate, synthesis and edit carries a map from every source occurrence
to an exact target span. Missing, extra, duplicated, overlapping, stale or
wrong renderings fail before persistence. Model prose claiming compliance has
no effect.

## Review evidence and blindness

The blind-review payload contains opaque option keys, target text and term
maps. Candidate IDs, lane labels, translator notes, translator provider and
translator model are absent.

Review categories cover MQM-style accuracy, mistranslation, omission,
addition, untranslated content, terminology, names, numbers, negation and
modality, logic, references, source retention, register, style, fluency,
Chinese expression, punctuation, protected structure and cohesion. Serious
unit findings must cite an exact source or target offset, not merely repeat a
substring. The same constraint applies to verification.

The verifier implements the useful part of MQM-APE: an edit is not accepted
merely because a critic proposed it. The verifier explicitly compares the
adjudicated and edited versions and blocks a degrading edit.

## Identity, persistence and recovery

Actor identity binds role, provider, model, prompt revision and canonical
non-secret settings. Secret-like settings are rejected. Plan identity binds
the exact M0 project, snapshot, manifest, ordered unit revisions, terminology
snapshot, context policy, source brief, lanes, per-unit routes and all review
actors.

Schema 3 adds only `m1_*` tables. M0 payloads are not rewritten. Terminology,
plans, contexts, candidates, reviews, adjudications, edits, verifications,
outcomes, global reports, releases and events are immutable. Each logical slot
accepts an identical replay and rejects different data. A restart reconstructs
and validates stored stages, then invokes services only for the first absent
stage.

## Atomic release

A release requires exactly one passed outcome for every translatable M0 unit,
one passing whole-document report, and one matching M0 approval per unit.
Outcome text and approval text must be identical.

Release persistence, approval-history insertion, replacement of every active
M0 approval pointer, and the M1 active-release marker occur in one SQLite
transaction. A crash cannot expose a partially activated book. Replaying the
same release is idempotent.

## Implemented proof

The checkpoint tests evidence thresholds, mainstream overrides, unresolved
retention, all four rendering policies, first use, stale and overlapping
occurrences, exact application coverage, adaptive routing, source-only context,
blind payloads, exact citations, disguised selection edits, contradictory
verdicts, additive schema migration, immutable-slot conflicts, crash recovery,
clean resume and atomic release activation.

Together with the M0 and BabelDOC synthetic-PDF regressions, the repository
currently executes 64 passing tests.
