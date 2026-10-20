# Code Assistant Agent

A streamlined, custom-built autonomous coding agent system designed with a minimal core and an isolated workspace model.

This project is built from the ground up, separating the core agent engine from the sandboxed environment where the agent performs its work. The architecture is cleanly divided into:
* **`code-assistant-agent/layer/`**: The core execution engine, tooling layers, and sequential system upgrades.
* **`code-assistant-agent/output/`**: The isolated sandbox directory where the agent reads/writes code, executes terminal tasks, and maintains logs.

---

## Directory Structure & Architectural Layers

The capabilities of the agent are built sequentially across progressive folders in the `layer/` directory:

| Layer / Path | Focus Area | Description |
| :--- | :--- | :--- |
| **`layer/core.py`** | **Core Engine** | The central "brain" connecting to the LLM, managing the perceived state, and dispatching tool commands. |
| **`1. the_basic_loop/`** | **Perception & Action** | Basic perception-action loops, raw tool use, task file writing (`todo.md`), and basic sub-agent spawning. |
| **`2. memory_and_knowledge/`** | **State & Skills** | Dynamic skill loading, prompt history summarization/compaction, and multi-dependent task execution frameworks. |
| **`3. teamwork_and_background_jobs/`** | **Concurrency & Multi-Agent** | Running slow shell tasks in the background, communicating between specialized agents via text-based mailbox queues, and isolating git branches per task. |
| **`4. getting_ready_for_real_users/`** | **UX & Safety** | Real-time terminal streaming, safe file tools with backups, human-in-the-loop verification, system event buses, and session persistence. |
| **`5. making_it_super_fast/`** | **Performance & Protocols** | Parallel tool execution, interrupt handling (`Ctrl+C`), prompt caching optimization (Anthropic), and Model Context Protocol (MCP) integrations. |
| **`6. final_enterprise_upgrades/`** | **Robustness** | Production-grade state database (SQLite/Redis) queues, auto-creation of git branches/pull requests, and robust teamwork patterns. |

---

## Output Sandbox Isolation

To guarantee your development repository remains clean and protected, the agent operates in a strict sandbox environment:
* **File Operations**: When the agent launches, the current directory context shifts to the `output/` folder. All reading, writing, and editing are locked here.
* **Session Persistence**: Saved conversations (`.sessions/`) are stored locally under `output/.sessions/`.
* **State Files**: Shared structures like `.agent_tasks.json`, `.mailboxes/`, and `.agent_memory.md` are isolated from your staging git index.

---

## Getting Started

### 1. Prerequisites & Virtual Environment
From the workspace root, set up and activate your virtual environment:
```powershell
# Create venv
python -m venv .venv

# Activate venv (PowerShell)
.venv\Scripts\Activate.ps1
```

Navigate into the `code-assistant-agent` project directory and install the requirements:
```powershell
cd code-assistant-agent
pip install -r requirements.txt
```

### 2. Environment Configuration
Create a `.env` file in the `code-assistant-agent/` directory. Configure it using one of the two setups below depending on your model choice:

#### Setup A: Direct Anthropic (Claude Models)
Use this configuration if you want to connect directly to the Anthropic API:
```ini
# Add your Anthropic API Key
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# (Optional) Override default model (default is claude-3-5-sonnet-20240620)
MODEL_ID=claude-3-5-sonnet-20240620
```

#### Setup B: OpenAI via LiteLLM Proxy (GPT Models)
Use this configuration if you want to run GPT models (e.g., `gpt-4o-mini`) via LiteLLM:
```ini
# Add your OpenAI API Key
OPENAI_API_KEY=your_openai_api_key_here

# Route Anthropic SDK requests to the local LiteLLM proxy
ANTHROPIC_BASE_URL=http://localhost:4000

# Specify the custom model mapped in litellm_config.yaml
MODEL_ID=my-custom-model
```

---

## Running the Agent

### Running with Anthropic (Direct)
Simply start the session management script:
```powershell
python "layer\4. getting_ready_for_real_users\17_session_management.py"
```

### Running with OpenAI (via LiteLLM Proxy)
1. **Start the LiteLLM Proxy Server** in a separate terminal:
   ```powershell
   litellm --config litellm_config.yaml
   ```
2. **Launch the Agent** in your main terminal:
   ```powershell
   python "layer\4. getting_ready_for_real_users\17_session_management.py"
   ```

---

## Special REPL Commands
Once the agent starts, you can interact with it using these special session commands in the prompt:
* `:sessions` - List all saved sessions.
* `:resume <id>` - Continue a saved conversation by its 8-character ID.
* `:fork <id>` - Fork a previous session into a new conversation thread.
* `:title <text>` - Give the current session a human-readable title.
* `:save` - Manually save the conversation state to disk.