# srl-explorer

AI-powered CLI so easy a manager could do it.  

Demonstrates an REPL based AI agent for querying Nokia SR Linux network telemetry using natural language as the primary interface.

## Why this repo exists

This is a teaching repo. It exists to show engineers how AI agents work by letting them read the code, run it, and modify it in a space they are already familiar with: networking.

The target audience is engineers who are curious about AI, or maybe getting pushed toward “do something with AI.” If that is you, or someone on your team, this is meant to be the thing you clone and step through before reaching for a framework.

There are many aspects to LLMs and the tooling built around them. One of the most visible is what people generally call prompt engineering. I dislike the term. A lot of it feels less like science and more like prompt art. One experiment worth trying is to change the system prompt in `prompts.py`: tear it apart, put bogus information in it, reduce it to one line, and see how the behavior changes. That alone teaches a lot.

The overall pattern used by this code is what is typically called an agent loop. More broadly, some variation of that loop sits at the heart of many modern AI assistant tools: maintain state, let the model choose actions, execute them in host code, feed the results back, and repeat. This repo exists to make that loop visible and understandable rather than hiding it behind a framework.

What the agent loop actually is

At this level, the pattern comes down to a few key ideas:

- Conversation transcript as state; the model sees the full history of messages (system, user, assistant, tool) on every call. That is how it maintains context across turns. 
- Prompt + tool schema as the model contract; the system prompt tells the model what it knows and how to think, and the tool definitions tell it what it can do. Together they form the model’s operational playbook. 
- The tool-call / tool-result loop; the model returns structured tool calls, the agent executes them, feeds results back as tool-role messages, and the model decides whether to call more tools or produce a final answer. 
- Everything else is engineering discipline; reasoning extraction, structured logging, result truncation, and iteration limits. Important, but secondary to the loop itself.

The distilled agent loop in this repo is small. The rest of the codebase is the engineering around it. Both are worth understanding.

This codebase intentionally avoids using LangChain. Frameworks are good at collapsing ceremony. That is useful after someone understands the ceremony. In networking terms, LangChain-first is a bit like teaching EVPN only through vendor CLI config. People can deploy it, but they do not know what control-plane objects are moving underneath. When something breaks or behaves unexpectedly, they do not have the mental model to debug it.

The only thing between your code and the model is the OpenAI SDK. No framework, no agent library. The loop, the tool dispatch, and the history management are all code you can read and modify. The tool implementations also look like normal network automation code: call `gnmic`, query Prometheus, search YANG, return structured data. That is a deliberate bridge from automation Python into agent-loop Python. 

Once you understand the loop, frameworks make a lot more sense.

Intentional simplifications

This repo makes choices that favor learning over production readiness:

- Hardcoded topology and credentials. The lab topology and device credentials are hardcoded in `config.py` and referenced in the system prompt. This keeps the code simple and avoids configuration-management ceremony that would distract from the agent pattern. Do not do this in production. 
- Hardcoded system prompt. The system prompt is a large static string with the full lab topology baked in. This is intentional. It serves as the model’s complete operational playbook for a fixed lab environment. In a production system you would template or generate parts of it.

This lab also leans heavily on `srl-telemetry-lab` from `srl-labs`, another excellent tool for learning about telemetry.

## What it does

srl-explorer is a Python CLI agent with an interactive REPL. You ask questions about your network in plain English, and the agent reasons about which tools to use, executes them, and returns a synthesized answer. It uses OpenAI function calling with three tools:

- **gnmic** -- live gNMI queries against SR Linux devices
- **Prometheus** -- historical metrics and time-series trends
- **YANG model search** -- path discovery across 488+ SR Linux YANG models

```
srl> show me BGP neighbors on leaf1

  >>> The user is asking about current BGP neighbor state on leaf1.
  >>> This is live operational data, so I'll use gnmic_get.
  >>> I know the path: /network-instance[name=default]/protocols/bgp/neighbor
  >>> gnmic_get(target=leaf1, path=/network-instance[name=default]/protocols/bgp/neighbor)

Leaf1 has 2 established BGP neighbors in network-instance "default":

| Peer Address | AS | State | Received Routes |
|---|---|---|---|
| 192.168.11.1 | 201 | established | 5 |
| 192.168.12.1 | 202 | established | 5 |
```

The agent shows its reasoning (the `>>>` lines) and tool calls in real time before presenting the final answer.

## Prerequisites

- a Linux enviroment, it can be made to work on OSX or WSL but the user is on thier own in that regard.
- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- [containerlab](https://containerlab.dev/install/) for running the lab topology
- [gnmic](https://gnmic.openconfig.net/install/) CLI installed and on PATH
- A running [srl-telemetry-lab](https://github.com/srl-labs/srl-telemetry-lab) environment
- OpenAI API key

## Installation

```bash
git clone https://github.com/hyposcaler/srl-explorer.git
cd srl-explorer
make setup
```

`make setup` clones the SR Linux YANG models (if not already present) and installs Python dependencies. The YANG models version defaults to `v24.10.1` — override with `make setup YANG_MODELS_TAG=<version>`.

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model to use |
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus server URL |
| `CONTEXT_WINDOW` | `128000` | Max context window in tokens (should match your model) |
| `YANG_MODELS_DIR` | `./srlinux-yang-models` | Path to YANG models directory |
| `YANG_CACHE_DIR` | `.cache` | Cache directory for parsed YANG index |
| `SRL_EXPLORER_LOGS_DIR` | `./logs` | Directory for session logs |

> **Note:** `.env.example` includes only the most commonly configured variables. `YANG_MODELS_DIR`, `YANG_CACHE_DIR`, and `SRL_EXPLORER_LOGS_DIR` have sensible defaults and only need to be set if you want to override them.

## Usage

Start the REPL:

```bash
uv run srl-explorer
```

### Slash commands

- `/help` -- Show help
- `/clear` -- Clear conversation history
- `/quit` -- Exit

### Example queries

**Live state (gnmic):**
- "show me BGP neighbors on leaf1"
- "what's the admin state of ethernet-1/49 on spine1"
- "get the hostname of all leaves"

**Historical metrics (Prometheus):**
- "show CPU usage on all spines over the last hour"
- "what's the traffic rate trend on leaf1 uplinks"
- "are there any interface errors increasing on spine2"

**YANG discovery:**
- "what YANG paths exist for LLDP"
- "find counters related to VXLAN"

**Multi-tool chaining:**
- "is there any packet loss between leaf1 and spine1" (discovers counters via YANG, queries via gnmic/Prometheus)

**Explicit tool requests:**
- "use gnmic to get /system/name from all leaves"
- "query prometheus for rate(interface_statistics_in_octets{source='leaf1'}[5m])"

## Logging

Session logs are written to `./logs/` (configurable via `SRL_EXPLORER_LOGS_DIR`). Each session gets a timestamped directory, with each conversational turn logged as individual JSON files:

```
logs/
  session_2026-03-29T12-00-00/
    turn_001/
      00_user_message.json
      01_llm_reasoning.json
      02_tool_call_gnmic_get.json
      03_tool_result_gnmic_get.json
      04_llm_response_final.json
    session_summary.json
```

`session_summary.json` tracks total turns, tool call counts, token usage, and errors.

## Development

Run `make` to see all available targets:

| Target | Description |
|---|---|
| `make setup` | Install deps + clone YANG models |
| `make run` | Run srl-explorer locally |
| `make audit` | Check dependencies for known vulnerabilities (pip-audit) |
| `make lint` | Run linter (ruff) |
| `make format` | Format code (ruff) |
| `make test` | Run tests (pytest) |
| `make clean` | Remove caches, logs, build artifacts |
| `make docker-build` | Build the Docker container |
| `make docker-run` | Run in container (--network host, --env-file .env) |
| `make docker-shell` | Shell into the container for debugging |

### Docker

The Dockerfile uses a multi-stage build with gnmic and the YANG models baked in:

```bash
make docker-build
make docker-run
```

`docker-run` uses `--network host` so the container can reach Prometheus and lab devices. Logs are bind-mounted to `./logs/` on the host.

## Testing

Run the test suite:

```bash
make test
```

Tests mock the OpenAI API and verify the agent loop, tool dispatch, result truncation, iteration limits, reasoning extraction, and malformed tool argument handling.

## Extending

See [docs/adding-a-tool.md](docs/adding-a-tool.md) for a step-by-step guide on adding new tools to srl-explorer.

## Troubleshooting

**`gnmic: command not found`** -- Install gnmic and ensure it's on your PATH. See https://gnmic.openconfig.net/install/

**YANG index build is slow** -- First run parses all YANG files (~5s). Subsequent runs use a cached index from `.cache/` (~34ms). Delete `.cache/` to force a rebuild.

**`Connection refused` from Prometheus** -- Ensure srl-telemetry-lab is running and Prometheus is accessible at the configured URL (default: http://localhost:9090).

**OpenAI API errors** -- Check that `OPENAI_API_KEY` is set correctly in `.env`. Verify your API key has access to the configured model.

**YANG models directory not found** -- Run `make yang-models` to clone the SR Linux YANG models repository.
