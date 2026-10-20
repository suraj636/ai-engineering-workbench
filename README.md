# AI Engineering Workbench

A categorized, hands-on workspace for AI engineering. Code is organized into three top-level tracks: agents, ML pipelines, and deep-learning pipelines. Each track is divided into functional subcategories, and every project keeps its own README and runnable structure.

The original GitHub repositories remain the upstream source of truth and are linked below. This repository contains working snapshots of the selected projects plus the navigation layer.

**Last synced:** 2026-10-20 | **Public repositories:** 18 | **Projects imported:** 12 (11 implemented + 1 scaffold) | **Linked only:** 4 | **Meta:** 2

## Structure

```text
ai-engineering-workbench/
├── .gitignore
├── README.md
├── AGENTS.md                # local, git-ignored
│
├── agents/
│   ├── README.md
│   ├── coding/code-assistant-agent/
│   ├── data/dbpilot-platform/
│   ├── media/infographic-media-generator/
│   └── rag/
│       ├── transaction-categorization-agent/
│       └── rag-hybrid-search/
│
├── ml-pipelines/
│   ├── README.md
│   ├── financial/financial-ai-pipelines/
│   ├── nlp/
│   │   ├── comment-polarity-analyzer/
│   │   ├── sentiment-analysis-social-media/
│   │   └── textual-analysis-articles/
│   └── recommenders/recommendation-systems/
│
└── dl-pipelines/
    ├── README.md
    ├── pretraining/jax-pretraining-workflow/
    ├── fine-tuning/gemma-3-finetuning/
    └── inference/             # reserved for inference optimization
```

## Agents

Agentic systems and applied LLM applications: planning loops, tool use, retrieval, memory, permissions, and orchestration.

| Local project | Source | Purpose | Status |
|---|---|---|---|
| [agents/coding/code-assistant-agent](agents/coding/code-assistant-agent) | [GitHub](https://github.com/suraj636/code-assistant-agent) | From-scratch coding agent built in 23 progressive layers: tool loop, skills, memory compaction, subagents, background jobs, permissions, MCP, sessions, and git-worktree isolation. | Implemented |
| [agents/data/dbpilot-platform](agents/data/dbpilot-platform) | [GitHub](https://github.com/suraj636/dbpilot-platform) | Natural-language-to-SQL analytics platform over DuckDB with LangChain, a FastAPI/vanilla-JS UI, and a synthetic-data + LLM-as-a-judge evaluation harness. | Implemented |
| [agents/media/infographic-media-generator](agents/media/infographic-media-generator) | [GitHub](https://github.com/suraj636/infographic-media-generator) | Google ADK/Gemini agent that turns a blog URL into an infographic, evaluates the result, and regenerates with feedback. | Implemented |
| [agents/rag/transaction-categorization-agent](agents/rag/transaction-categorization-agent) | [GitHub](https://github.com/suraj636/transaction-categorization-agent) | Django REST service that categorizes transactions using similarity-retrieved few-shot examples, provider-agnostic LLM clients, JSON validation, and confidence calibration. | Implemented |
| [agents/rag/rag-hybrid-search](agents/rag/rag-hybrid-search) | [GitHub](https://github.com/suraj636/rag-hybrid-search) | Placeholder for hybrid retrieval experiments; currently README only. | Scaffold |

## ML Pipelines

Classical machine-learning and NLP pipelines: data preparation, model training, evaluation, serialization, and inference.

| Local project | Source | Purpose | Status |
|---|---|---|---|
| [ml-pipelines/financial/financial-ai-pipelines](ml-pipelines/financial/financial-ai-pipelines) | [GitHub](https://github.com/suraj636/Financial--AI-Pipelines) | Three scikit-learn pipelines for credit-risk classification, credit-score regression, and customer segmentation, with preprocessing, baselines, metrics, joblib artifacts, and inference scripts. | Implemented |
| [ml-pipelines/nlp/comment-polarity-analyzer](ml-pipelines/nlp/comment-polarity-analyzer) | [GitHub](https://github.com/suraj636/CommentPolarityAnalyzer) | TensorFlow/Keras BiLSTM multi-label toxic-comment classifier trained on Jigsaw data, with a Gradio demo. | Implemented |
| [ml-pipelines/nlp/sentiment-analysis-social-media](ml-pipelines/nlp/sentiment-analysis-social-media) | [GitHub](https://github.com/suraj636/SentimentAnalysisOnSocialMediaPosts) | Depression-text classification using NLTK preprocessing, TF-IDF features, and Naive Bayes/Random Forest models. | Implemented |
| [ml-pipelines/nlp/textual-analysis-articles](ml-pipelines/nlp/textual-analysis-articles) | [GitHub](https://github.com/suraj636/TextualAnalysisOnArticles) | Web-scraping and NLP text analytics pipeline computing sentiment polarity, readability (Fog Index), word statistics, and pronoun counts into a structured CSV. | Implemented |
| [ml-pipelines/recommenders/recommendation-systems](ml-pipelines/recommenders/recommendation-systems) | [GitHub](https://github.com/suraj636/Recommendation_Systems) | Two recommender notebooks: movie content similarity with CountVectorizer and cosine similarity, plus a book popularity/collaborative-filtering recommender. | Implemented |

## DL Pipelines

Deep-learning and LLM training workflows: pretraining, fine-tuning, and future inference optimization.

| Local project | Source | Purpose | Status |
|---|---|---|---|
| [dl-pipelines/pretraining/jax-pretraining-workflow](dl-pipelines/pretraining/jax-pretraining-workflow) | [GitHub](https://github.com/suraj636/jax-pretraining-workflow) | JAX/Flax NNX MiniGPT (~3.5M parameters) pretrained from scratch on TinyStories with custom BPE, checkpoints, loss curves, and Gradio inference. | Implemented |
| [dl-pipelines/fine-tuning/gemma-3-finetuning](dl-pipelines/fine-tuning/gemma-3-finetuning) | [GitHub](https://github.com/suraj636/gemma-3-finetuning) | Parameter-efficient Gemma 3 fine-tuning with Unsloth, 4-bit loading, and LoRA on the FineTome-100k conversational dataset. | Implemented |

## Linked but not imported

These repositories remain on GitHub and are linked from this workbench, but they are not part of the three code tracks yet.

| Repository | Reason | Status |
|---|---|---|
| [system-prompts-and-models-of-ai-tools](https://github.com/suraj636/system-prompts-and-models-of-ai-tools) | Forked reference collection of agent system prompts and tool schemas from v0, Cursor, Manus, Lovable, Devin, and Replit. | Reference fork |
| [ai-interview-prep](https://github.com/suraj636/ai-interview-prep) | Placeholder repository for AI interview preparation material; currently README only. | Scaffold |
| [VideoConvertor](https://github.com/suraj636/VideoConvertor) | Forked Flask/FFmpeg video converter; a utility, not an AI project. | Fork, non-AI |
| [Basic-Banking-System](https://github.com/suraj636/Basic-Banking-System) | HTML/CSS/JavaScript banking demo built for The Sparks Foundation; a legacy web project. | Archive, non-AI |

## Meta

| Repository | Role |
|---|---|
| [ai-engineering-workbench](https://github.com/suraj636/ai-engineering-workbench) | This workspace: code tracks, taxonomy, index, and operating rules. |
| [suraj636](https://github.com/suraj636/suraj636) | GitHub profile README describing AI engineering focus and selected projects. |

## Roadmap

- **Inference Optimization & Serving** - `dl-pipelines/inference/` is reserved for quantization, KV-cache optimization, batching, speculative decoding, latency/throughput benchmarks, and serving infrastructure.
- **Evaluation & Observability** - a standalone evaluation harness, tracing, regression testing, and quality dashboards. `dbpilot-platform` contains an evaluation harness, but there is no dedicated evaluation repository yet.
- **Production LLM Infrastructure** - deployment patterns, guardrails, cost controls, rate limiting, and monitoring.
- **RAG track** - `agents/rag/` is a subcategory for now. Promote it to a top-level `rag/` track when it contains multiple implemented retrieval systems.

## Status legend

- **Implemented** - source code and a usable README are present.
- **Scaffold** - README or placeholder only; no implementation exists to evaluate.
- **Reference fork** - forked upstream reference material; not original work.
- **Fork, non-AI** - a forked utility outside the AI engineering track.
- **Archive, non-AI** - a legacy project kept for context.
- **Meta** - a portfolio-level repository.

## Import notes

- The 12 imported projects are static snapshots taken from their default branches on 2026-10-20.
- `.git` metadata was removed from the snapshots; generated Python `__pycache__` and `.pyc` files were excluded.
- The original GitHub repositories were not moved, renamed, deleted, or modified.
- Forks and linked-only repositories are labeled and credited to their upstream projects.
- Some project READMEs reference files that are not present in their upstream repositories; these discrepancies are noted in the local, git-ignored `AGENTS.md`.
- Private repositories, if any, are not visible through the unauthenticated GitHub API and are not included.
