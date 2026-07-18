# Terminology evidence format

The runtime does not accept a target-only glossary as proof. Every governed
term starts with an exact source expression, a document-specific sense, one or
more Chinese candidates, captured evidence, and an independent review.

## Import shape

Pass a UTF-8 JSON file with an `entries` array to `pubtrans translate` using
`--evidence`. See `examples/evidence.example.json` for the complete shape.

Each entry records:

| Field | Meaning |
| --- | --- |
| `source_term` | Exact, case-sensitive source substring |
| `sense_id` | Stable identifier for this document sense |
| `target_form` | One candidate Chinese rendering |
| `stance` | Discovery stance: `SUPPORTS` or `CONTRADICTS` |
| `kind` / `tier` | Source type and initial evidence tier |
| `source_key` | Stable record identity; use a digest, not a row number |
| `source_uri` | HTTP(S) URL or durable URN |
| `source_title` | Human-auditable source title |
| `excerpt` | Short exact captured passage containing the candidate |
| `retrieved_on` | ISO date (`YYYY-MM-DD`) |
| `sense_match` / `domain_match` | Import metadata only; the independent reviewer reassesses both |
| `notes` | Capture method, edition, page, caveat, or counterevidence note |

The operator is responsible for the authenticity of manually imported
excerpts and source tiers. Web research is safer by default: a provider
citation is discovery material only, the runtime fetches the cited page, and
the candidate must occur in the captured page text before the record reaches
review.

## Evidence ladder

| Tier | Typical material | What it can establish |
| --- | --- | --- |
| A | Termonline, CNTERM, applicable standards terminology | Strong standardization evidence after sense/domain review |
| B | Official naming or domain-primary Chinese source | Strong domain or proper-name evidence |
| C | Independent parallel publication or real corpus usage | Conventionality and corroboration |
| D | Reputable dictionary or general reference | Candidate discovery and limited support |
| E | Search snippet, forum, vendor copy, unattributed list | Discovery only; never sufficient for verified status |

Two URLs are not automatically two sources. For HTTP(S) records the runtime
groups evidence by publishing host; known Termonline, CNTERM and `gov.cn`
subdomains are conservatively grouped under their parent authority. Pages from
one group count once toward corroboration.

## Deterministic confidence

The model cannot assign its own confidence label. Code derives it after the
independent reviewer has marked stance, exact sense and domain:

- `VERIFIED`: at least two independent qualified sources, at least one A/B
  source, established or attested usage, and no authoritative contradiction;
- `SUPPORTED`: qualified strong evidence or corroborated independent usage;
- `PROVISIONAL`: at least one qualified attestation;
- `RETAINED_UNRESOLVED`: no qualified support, so the source expression stays.

Verified terms may appear as Chinese only. Supported and provisional terms
show the source on first use. Unresolved terms remain in the source language.
A rarer candidate cannot displace a more conventional confirmed form without
an explicit accuracy reason.

## Capture rules

1. Capture the smallest excerpt that proves actual usage without removing the
   sense-defining context.
2. Keep editions, page numbers and access dates in `notes` for non-web sources.
3. Record counterevidence, homographs and other-domain uses; do not curate only
   supporting examples.
4. Use different `sense_id` values for genuinely different senses. If one exact
   source form is assigned multiple unresolved senses, the planner omits the
   automatic term rule instead of applying one globally.
5. Never upgrade a search snippet, LLM recollection, or candidate list into an
   authority record.
