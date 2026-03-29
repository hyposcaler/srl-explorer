# Project: srl-explorer

## Overview

Build a Python CLI agent with a REPL interface for querying live Nokia SR Linux network telemetry. The agent uses an OpenAI LLM to reason about user questions and decide whether to answer using gnmic (real-time device state via gNMI), Prometheus (historical metrics via PromQL), or both. It can also discover valid YANG paths by searching the SR Linux YANG models bundled in the project. It targets the srl-labs/srl-telemetry-lab environment.

## Architecture

### Core Loop

- REPL interface using prompt_toolkit for readline-style input with history, completion, and multi-line support
- Agent loop: user query → LLM reasoning → tool selection/execution → LLM synthesis → response
- The LLM should autonomously decide which tool(s) to use based on the question, but the user can also explicitly request a specific tool (e.g., "use gnmic to show me the interface config" or "query prometheus for CPU over the last hour")

### Tools (OpenAI function calling)

#### gnmic_get

- Shells out to the `gnmic` CLI binary (assumed available on PATH)
- Read-only: `gnmic get` only, no set operations
- Parameters: target (device name/address), path (YANG path), encoding (json/json_ietf), optional flags
- The agent should know the lab topology (srl1, srl2, srl3) and map natural language device references to targets
- Return parsed JSON output to the LLM for reasoning

#### prometheus_query

- Queries the Prometheus HTTP API (`/api/v1/query` and `/api/v1/query_range`)
- The agent constructs PromQL from natural language — it should understand the metric names available from the srl-telemetry-lab stack (gnmic exports to Prometheus via gnmic's prometheus output)
- Parameters: query (PromQL string), time/start/end/step for range queries
- Support both instant and range queries
- Return formatted results to the LLM

#### yang_search

- Searches the SR Linux YANG models in `srlinux-yang-models/` to discover valid gNMI paths
- At startup, pre-parse all YANG models into a searchable index of (xpath, type, description) using pyang or equivalent YANG parser
- The agent calls this tool with a keyword or feature area (e.g., "bgp", "interface counters", "cpu", "lldp") and gets back matching YANG paths with their types and descriptions
- Parameters: keyword (search string), optional module filter, max_results (default 20)
- Return matching paths sorted by relevance so the agent can pick the right one for a gnmic_get call
- This enables a discover-then-query workflow: agent doesn't know the exact path → searches YANG → uses the discovered path with gnmic

### LLM Integration

- OpenAI Chat Completions API with function calling / tool use
- System prompt should include:
  - Lab topology context (3x SR Linux nodes: srl1, srl2, srl3, connected in a leaf-spine or chain topology per the lab)
  - A small set of commonly used gNMI paths for quick access (e.g., /interface, /network-instance, /system, /platform) — the agent should use yang_search for anything beyond these
  - Prometheus metric naming conventions from gnmic's prometheus output (metric name patterns, common labels)
  - Guidance on tool selection:
    - yang_search: when the agent needs to discover or verify a valid YANG path before querying
    - gnmic: current config/state, operational data, real-time values
    - Prometheus: time-series trends, historical data, aggregations, rate calculations
  - The agent may chain tool calls: yang_search → gnmic_get, or yang_search → prometheus_query, or gnmic + prometheus together
- Multi-turn tool use: the agent may chain multiple tool calls before synthesizing a final answer
- Model: configurable, default to gpt-4o

### YANG Model Index

- Located in `srlinux-yang-models/` subdirectory (contains the YANG files for the SR Linux version running in the lab)
- At startup, parse all `.yang` files and build an in-memory index
- Index fields per entry: full xpath, node type (leaf, container, list), YANG type (string, uint32, counter64, etc.), description text, module name
- Use pyang as a dependency for parsing — it handles imports, augmentations, and deviations correctly
- Index should support keyword search across path components and descriptions
- Consider caching the parsed index (e.g., pickle or JSON) so subsequent startups are fast, with a hash check to invalidate when YANG files change

### Project Setup

- Python with uv for dependency management
- pyproject.toml with dependencies: openai, httpx (for Prometheus API), prompt_toolkit, rich (for terminal output formatting), pyang (for YANG parsing)
- Config via environment variables and/or a .env file:
  - OPENAI_API_KEY
  - PROMETHEUS_URL (default: http://localhost:9090)
  - GNMIC_DEFAULT_TARGET (optional)
  - YANG_MODELS_DIR (default: ./srlinux-yang-models)
- Entry point: `srl-explorer` CLI command via pyproject.toml scripts

### UX Details

- Rich-formatted output: use rich for tables, syntax-highlighted JSON, and markdown rendering in the terminal
- Show tool calls being made (which tool, what parameters) so the user can see the agent's reasoning
- Support `/help`, `/quit`, `/clear` slash commands
- Conversation history maintained within session for multi-turn context
- Show a startup message indicating how many YANG paths were indexed

### Directory Structure

    srl-explorer/
    ├── pyproject.toml
    ├── README.md
    ├── .env.example
    ├── srlinux-yang-models/    # SR Linux YANG models (user-provided)
    ├── src/
    │   └── srl_explorer/
    │       ├── __init__.py
    │       ├── cli.py           # REPL loop and prompt_toolkit setup
    │       ├── agent.py         # Agent loop, LLM interaction, tool dispatch
    │       ├── tools/
    │       │   ├── __init__.py
    │       │   ├── gnmic.py     # gnmic CLI wrapper
    │       │   ├── prometheus.py # Prometheus HTTP API client
    │       │   └── yang.py      # YANG model parser, indexer, and search
    │       ├── prompts.py       # System prompt and tool definitions
    │       └── config.py        # Configuration and env loading

## Constraints

- Do NOT use litellm
- Use uv for all Python dependency management
- Shell out to gnmic binary, do not use a gNMI Python library
- Read-only operations only — no gnmic set, no Prometheus write
- Keep the agent loop simple and debuggable — log tool calls and responses