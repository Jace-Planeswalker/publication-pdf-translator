---
name: translate-publication-pdf
description: Create, resume, inspect, or repair a complete publication PDF translation that must preserve the source layout through BabelDOC, use evidence-governed terminology, and emit a verified final PDF. Use for books, papers, reports, manuals, and other full PDFs; do not use for short passages, summaries, ordinary explanations, or requests whose primary deliverable is DOCX or LaTeX.
---

# Translate a publication PDF

Use the `publication-pdf-translator` project as the only workflow authority.
This Skill launches and monitors it; do not translate units in chat, build a
parallel ledger, or substitute another PDF/LaTeX/DOCX layout pipeline.

## Run or resume

1. Resolve the source PDF, a durable project directory, product config and any
   operator-supplied terminology evidence. Never place source books, outputs,
   credentials or state databases in the Git repository.
2. Ensure `pubtrans` comes from the public
   `Jace-Planeswalker/publication-pdf-translator` project with its `babeldoc`
   extra. Do not silently use stock BabelDOC; the project pins its provider
   fork.
3. Read credentials only from the environment-variable name in config. Never
   print, persist or place a secret in command arguments or JSON.
4. Inspect existing state first:

   ```bash
   pubtrans status <project-directory>
   ```

5. Start or resume the complete build:

   ```bash
   pubtrans translate <source.pdf> \
     --project <project-directory> \
     --config <config.json> \
     --evidence <evidence.json>
   ```

   Omit `--evidence` only when no captured manual evidence exists; configured
   web research may still discover and harvest public sources.
6. Continue until the command returns `RELEASED` or a truthful blocker. On
   interruption, run the same command again. Do not delete state or force a
   fresh run merely to make progress appear cleaner.
7. Re-run `status`. Return the `*.verified.pdf` and
   `verification-report.json` only when status remains `RELEASED`.

## Terminology rules

- Treat model memory, search snippets and citation text as discovery, not
  evidence. For web material, require an actually fetched page excerpt.
- Prefer exact sense and domain fit, then mainstream Chinese usage. Do not
  reward specialist-sounding rarity.
- Do not describe two pages from one publisher as independent corroboration.
- Let the runtime derive confidence. Unsupported candidates must expose or
  retain the source expression; never edit state to force a Chinese form.
- If an important source is inaccessible, capture a short exact excerpt,
  durable URI/URN, title, date, edition/page and caveat in the evidence file.

## Blockers and repair

Read structured stderr, `pubtrans status`, and
`output/verification-report.json`. Repair the actual cause—credential/model
configuration, missing evidence, provider failure, protected structure,
translation finding, font/layout defect, or mutated output—then resume the same
project. Never activate a blocked artifact, hand-edit a verified PDF, or report
success from a model's prose alone. If the published PDF changes, rerun the
runtime so artifact verification binds the new bytes.
