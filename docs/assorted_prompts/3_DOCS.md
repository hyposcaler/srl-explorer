# Task: Create Architecture Document and README for srl-explorer

## Context

srl-explorer is a Python CLI agent with a REPL interface for querying Nokia SR Linux network telemetry using natural language. It uses an OpenAI LLM with function calling to reason about and chain three tools: gnmic (live gNMI queries), Prometheus (historical metrics via PromQL), and YANG model search (path discovery). It targets the srl-labs/srl-telemetry-lab environment.

The `srlinux-yang-models/` directory is a git submodule pointing to the upstream Nokia SR Linux YANG models repo. This has implications for cloning — users must use `--recurse-submodules` or run `git submodule update --init` after cloning.

Review all source files in `src/srl_explorer/` to understand the full implementation before writing either document. The docs should reflect what the code actually does, not just the design intent.

## Document 1: ARCHITECTURE.md

Create `ARCHITECTURE.md` in the project root. This is a technical reference for contributors and anyone wanting to understand how the system works. Write it in markdown.

### Content to cover

- **Overview**: What srl-explorer is, the problem it solves, and the high-level approach (LLM-powered agent loop over network telemetry tools)
- **System Architecture**: How the components fit together — the REPL, agent loop, LLM integration, and tool execution. Include a description of the data flow from user input through tool chaining to final response.
- **Agent Loop**: Detail the agent loop in `agent.py` — how it handles multi-turn tool use, the while loop structure, how tool calls are dispatched, how the conversation history is managed, and how the LLM decides when to stop.
- **Tools**: Each tool gets its own subsection:
  - `gnmic_get`: How it shells out to the gnmic binary, target resolution from the topology map, timeout handling, credential management
  - `prometheus_query`: HTTP API client, instant vs range query logic, how PromQL is constructed by the LLM
  - `yang_search`: YANG model parsing pipeline (pyang), index building at startup, caching with hash invalidation, search/scoring algorithm
- **LLM Integration**: System prompt design, tool definitions (OpenAI function calling schema), tool selection guidance embedded in the prompt, how the LLM chains tools (yang_search → gnmic, etc.)
- **Structured Turn Logging**: The logging system — session/turn/file directory hierarchy, what gets logged at each step, token usage tracking, session summaries. Explain that logs capture full untruncated tool results even though the agent truncates for context window management.
- **Configuration**: Environment variables, config dataclass, topology and credential management, YANG model directory and cache
- **Key Design Decisions**: Why shell out to gnmic vs using a gNMI library, why pyang for YANG parsing, why the directory-per-turn logging structure, why async, why OpenAI function calling

## Document 2: README.md

Create `README.md` in the project root. This is for users who want to set up and use srl-explorer.

### Content to cover

- **Project title and one-line description**
- **What it does**: Brief explanation with a realistic example interaction showing the REPL in action (user asks a question, agent chains tools, returns an answer). Make up a plausible example based on the lab topology.
- **Prerequisites**:
  - Python 3.11+
  - uv
  - gnmic CLI installed and on PATH
  - A running srl-labs/srl-telemetry-lab environment (link to the repo)
  - OpenAI API key
- **Installation**:
  - Clone with submodules: `git clone --recurse-submodules <repo-url>`
  - Note: if already cloned without submodules: `git submodule update --init --recursive`
  - Install with uv: `uv sync` or `uv pip install -e .`
- **Configuration**:
  - `.env` file setup (copy from `.env.example`)
  - All environment variables with descriptions and defaults
  - Note about YANG_MODELS_DIR pointing to the submodule directory
- **Usage**:
  - Starting the REPL: `uv run srl-explorer`
  - Slash commands: /help, /clear, /quit
  - Example queries organized by tool type:
    - Live state queries (gnmic): "show me BGP neighbors on leaf1", "what's the admin state of ethernet-1/49 on spine1"
    - Historical metrics (Prometheus): "graph CPU usage on all spines over the last hour", "what's the traffic rate on leaf1 uplinks"
    - YANG discovery: "what YANG paths exist for LLDP", "find counters related to VXLAN"
    - Multi-tool chaining: "is there any packet loss between leaf1 and spine1" (agent discovers relevant counters via YANG, queries via gnmic or Prometheus)
    - Explicit tool requests: "use gnmic to get /system/name from all leaves"
- **Logging**: Where logs go, directory structure overview, how to find session summaries
- **Lab Topology Reference**: Quick diagram or table of the 5-node Clos fabric — devices, roles, addresses, interconnections
- **Troubleshooting**: Common issues — gnmic not found, YANG index build failures, Prometheus connection refused, OpenAI API errors

## Constraints

- Both documents should be written in clean, concise markdown
- Keep the README practical and scannable — users should be able to get running in 5 minutes
- The architecture doc should be thorough but not verbose — a developer should understand the system after one read
- Do not fabricate features that aren't in the code — read the source first
- Output both files to the project root