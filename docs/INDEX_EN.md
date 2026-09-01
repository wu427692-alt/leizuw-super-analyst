# Leziwu Super Value · Documentation

This is the engineering documentation index for Leziwu Super Value. Start with the [English overview](README_EN.md), the [Chinese project homepage](../README.md), or the [live user guide](https://app.leziwu.com/guide).

## Product and research

| Goal | Documentation |
| --- | --- |
| Understand the product loop | [Project homepage](../README.md) |
| Research a company or industry | [Company and industry research](industry-research.md) (Chinese) |
| Explore concept themes and Beta/Alpha attribution | [Concept-theme consensus engine](concept-theme-consensus.md) (Chinese) |
| Run event and institutional-text research | [Institutional-text quant research](essay-quant.md) (Chinese) |
| Filter and export research data | [One-stop data acquisition](data-acquisition.md) (Chinese) |
| Use the unified financial API | [Unified financial data API](financial-data-api.md) (Chinese) |

## Setup, deployment, and operations

| Goal | Documentation |
| --- | --- |
| Configure and run the full system | [Full guide](full-guide_EN.md) |
| Configure model providers | [LLM configuration](LLM_CONFIG_GUIDE_EN.md) |
| Deploy the service | [Deployment guide](DEPLOY_EN.md) |
| Operate the production server | [Cloud operations](cloud-operations.md) (Chinese) |
| Troubleshoot common issues | [FAQ](FAQ_EN.md) |
| Understand data-source fallback | [Data-source stability](data-source-stability.md) (Chinese) |

## Development and compatibility references

| Document | Scope |
| --- | --- |
| [API specification](architecture/api_spec.json) | Generated FastAPI OpenAPI contract |
| [Contributing guide](CONTRIBUTING_EN.md) | Issues, pull requests, tests, and documentation |
| [Decision signal compatibility reference](decision-signals.md) | Retained backend/API contract for legacy consumers |
| [Analysis Context Pack Contract, Runtime Consumption, And Visibility](analysis-context-pack.md) <sub><sub>![P6 Badge](https://img.shields.io/badge/P6-orange?style=flat)</sub></sub> (Chinese-only) | P1/P2 internal contracts, P3 prompt-summary consumption, P4 history/API/Web low-sensitivity visibility, P5 data-quality scoring, and P6 migration/rollback notes; the full guide contains the #1386 market-phase analysis, migration, and rollback entry points |
| [Changelog](CHANGELOG.md) | Product, data, deployment, and compatibility changes |

Historical English compatibility documents remain in the repository because automated contract tests and downstream deployments still reference them. They are not the primary product description; the current product truth is the root README, the live user guide, and the Chinese engineering index.
