# srl-explorer

AI-powered CLI so easy a manager could do it.  

That uses AI to query Nokia SR Linux network telemetry using natural language as the primary interface.

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
| `YANG_MODELS_DIR` | `./srlinux-yang-models` | Path to YANG models directory |
| `YANG_CACHE_DIR` | `.cache` | Cache directory for parsed YANG index |
| `SRL_EXPLORER_LOGS_DIR` | `./logs` | Directory for session logs |

> **Note:** `.env.example` includes only the three most commonly configured variables. `YANG_MODELS_DIR`, `YANG_CACHE_DIR`, and `SRL_EXPLORER_LOGS_DIR` have sensible defaults and only need to be set if you want to override them.

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

Tests mock the OpenAI API and verify the agent loop, tool dispatch, result truncation, iteration limits, and reasoning extraction.

## Extending

See [docs/adding-a-tool.md](docs/adding-a-tool.md) for a step-by-step guide on adding new tools to srl-explorer.

## Troubleshooting

**`gnmic: command not found`** -- Install gnmic and ensure it's on your PATH. See https://gnmic.openconfig.net/install/

**YANG index build is slow** -- First run parses all YANG files (~5s). Subsequent runs use a cached index from `.cache/` (~34ms). Delete `.cache/` to force a rebuild.

**`Connection refused` from Prometheus** -- Ensure srl-telemetry-lab is running and Prometheus is accessible at the configured URL (default: http://localhost:9090).

**OpenAI API errors** -- Check that `OPENAI_API_KEY` is set correctly in `.env`. Verify your API key has access to the configured model.

**YANG models directory not found** -- Run `make yang-models` to clone the SR Linux YANG models repository.
