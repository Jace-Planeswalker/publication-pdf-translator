# Research and adoption record

This project optimizes translation and restored-PDF quality, not loyalty to a
home-grown architecture. Mature ideas are copied or ported when their evidence
and license permit it; research stacks are not inherited wholesale when their
operational assumptions are weaker than ours.

## Adopt, port, or reject

| Project or evidence | What is adopted | What is not adopted |
| --- | --- | --- |
| [BabelDOC](https://github.com/funstory-ai/BabelDOC) | PDF IL, rich-text/formula protection, typesetting and layout reconstruction; maintained fork adds a document provider seam | No second LaTeX/DOCX layout pipeline; semantic approval remains outside BabelDOC |
| [AIDAterm](https://github.com/emanueledirosa/aida_t-acl2026-industrytrack) and [ACL paper](https://aclanthology.org/2026.acl-industry.63/) | Analysis → translation → conservative post-edit → review; terminology at every stage; heterogeneous-role discipline; public prompts retained verbatim as a research baseline | Its CAT batch contract, self-reported confidence and per-segment-only state are replaced by exact PDF-unit, evidence, blindness and recovery contracts |
| [GRAFT](https://github.com/himanshu-dutta/graft) | Source-side discourse locality and document memory concepts | Whole research runtime, naive segmentation, very large dependency set and unclear placeholder/layout contract |
| [Translation Agent](https://github.com/andrewyng/translation-agent) | Generate → critique → revise as a useful local improvement motif | Demo self-reflection is not independent approval and is not the kernel backbone |
| [TransAgents](https://github.com/minghao-wu/transagents) | Literary role specialization as an experiment; omission results as a warning | Agent count is not treated as quality; public omission failures rule out direct adoption |
| TACTIC | Context/research roles and literal/sense/free exploration for high-risk passages | CAMEL/vLLM research stack and automatic-metric-led release decisions |
| [COMET](https://github.com/Unbabel/COMET) | Optional DocCOMET/XCOMET signals, error-span hints and MBR ranking in later verification | No learned metric is a sole release gate or substitute for exact completeness |
| [MQM](https://github.com/google/wmt-mqm-human-evaluation), [mt-metrics-eval](https://github.com/google-research/mt-metrics-eval), [span meta-evaluation](https://github.com/amazon-science/span-mt-metaeval) | Typed severity/category vocabulary and evidence-oriented evaluator calibration | A score without cited, actionable evidence cannot block or approve text |
| MQM-APE | Verify whether a proposed edit actually improves the translation | Critic edits are never accepted without an independent comparison |
| Weblate and Translate Toolkit | Concept-oriented termbase, history, checks, TBX interoperability direction | Software-string units do not define publication segmentation or PDF write-back |
| [Termonline](https://www.termonline.cn/) / [CNTERM](https://www.cnterm.cn/) | Preferred authoritative evidence for standardized Chinese scientific and technical terms where coverage applies | Authority is still sense/domain checked; an unrelated same-spelling entry is not evidence |

## Why AIDAterm changes the design

AIDAterm reports that its sequential, terminology-aware architecture materially
outperforms simpler configurations, and that processing terminology per segment
is much more reliable than loose batch handling. That supports a sequential
quality bus and terminology injection at every stage. It does not justify
blindly copying its entire prompt contract: publication Chinese needs exact
source occurrences, protected BabelDOC structures, document discourse,
mainstream-use evidence, independent finding citations and crash recovery.

The four original prompt templates are therefore vendored under
`third_party/aida-term/` at source commit
`716f2e2532ad391fea206623716394333b1f25b1`. They remain CC BY-NC 4.0 and are
not relicensed by this repository. The project is currently research-only;
commercialization would require removing/replacing those materials or obtaining
permission.

## Terminology research ladder

The old pattern—read the book, search the web, choose a plausible Chinese
equivalent—fails because retrieval relevance is not conventionality or sense
fit. The new dossier separates candidate generation from approval:

1. define the concept and source sense in its local and document context;
2. collect multiple Chinese candidates, including source retention;
3. record supporting and contradicting evidence with stable source identity;
4. check semantic and domain match;
5. assess whether the form is established, merely attested, rare or unattested;
6. require an independent terminology reviewer;
7. expose uncertainty rather than manufacture precision.

Authority and official/domain-primary material outrank corpus evidence;
corroborated real usage outranks dictionary plausibility; search snippets and
LLM memory are discovery aids only. A rarer form can still win when it is more
accurate, but the exception must be explicit and auditable.

## Fork policy

Only BabelDOC is forked because the runtime needs a narrow IL write-back seam
that upstream does not currently expose. AIDAterm publishes prompts and
outputs, not a production runtime; its protected materials are vendored with
attribution. GRAFT, TransAgents, Translation Agent, COMET and localization
projects remain upstream dependencies or research references. Small mechanisms
are ported behind our contracts instead of importing their entire architecture.
