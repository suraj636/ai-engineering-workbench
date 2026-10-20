# Agents

Agentic systems and applied LLM applications. This track covers planning loops, tool use, retrieval, memory, permissions, and orchestration.

| Project | Source | Purpose | Status |
|---|---|---|---|
| [coding/code-assistant-agent](coding/code-assistant-agent) | [GitHub](https://github.com/suraj636/code-assistant-agent) | From-scratch coding agent built in 23 progressive layers: tool loop, skills, memory compaction, subagents, background jobs, permissions, MCP, sessions, and git-worktree isolation. | Implemented |
| [data/dbpilot-platform](data/dbpilot-platform) | [GitHub](https://github.com/suraj636/dbpilot-platform) | Natural-language-to-SQL analytics platform over DuckDB with LangChain, a FastAPI/vanilla-JS UI, and a synthetic-data + LLM-as-a-judge evaluation harness. | Implemented |
| [media/infographic-media-generator](media/infographic-media-generator) | [GitHub](https://github.com/suraj636/infographic-media-generator) | Google ADK/Gemini agent that turns a blog URL into an infographic, evaluates the result, and regenerates with feedback. | Implemented |
| [rag/transaction-categorization-agent](rag/transaction-categorization-agent) | [GitHub](https://github.com/suraj636/transaction-categorization-agent) | Django REST service that categorizes transactions using similarity-retrieved few-shot examples, provider-agnostic LLM clients, JSON validation, and confidence calibration. | Implemented |
| [rag/rag-hybrid-search](rag/rag-hybrid-search) | [GitHub](https://github.com/suraj636/rag-hybrid-search) | Placeholder for hybrid retrieval experiments; currently README only. | Scaffold |

## Subcategories

- `coding/` — software-engineering and coding agents.
- `data/` — agents that query, analyze, or operate on data.
- `media/` — agents that generate or process media.
- `rag/` — retrieval-augmented LLM systems. This is a subcategory for now; promote it to a top-level `rag/` track when it has multiple implemented projects.
