# Research notes

The architecture borrows bounded lessons rather than copying one project as a
complete solution.

| Project | Reused lesson | Deliberate limit |
| --- | --- | --- |
| [BabelDOC](https://github.com/funstory-ai/BabelDOC) | PDF IL, formula/rich-text placeholders, typesetting and layout reconstruction | It remains the layout engine; publication-level semantic workflow stays outside it |
| [PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate) and [2.0](https://github.com/PDFMathTranslate/PDFMathTranslate-next) | Packaging, CLI/UI deployment patterns, provider diversity and practical PDF translation integration | A convenient translator is not by itself an auditable book-level approval process |
| [Translation Agent](https://github.com/andrewyng/translation-agent) | Generate, critique, and revise as a useful candidate-improvement pattern | Its own README calls it demonstration software; self-reflection is not treated as independent review or approval |
| [Weblate](https://github.com/WeblateOrg/weblate) | Durable translation state, terminology, history, checks and review roles | Software localization units do not determine PDF book segmentation or layout write-back |

The central synthesis is therefore:

- use BabelDOC for layout fidelity;
- use a durable project database for recovery and auditability;
- use document context, sense-aware terminology, independent high-risk
  retranslation, and adjudication for translation quality;
- use deterministic and rendered-PDF gates to prove completeness instead of
  relying on model confidence or a single score.
