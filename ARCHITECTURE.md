# srl-explorer Architecture

## 1. Overview

srl-explorer is a Python CLI agent that lets network engineers query Nokia SR Linux telemetry using natural language. Instead of remembering gNMI paths, PromQL syntax, or YANG model structure, the user types a question in English and the agent figures out which tools to call, in what order, and how to synthesize the results.

The core approach is an LLM agent loop: user input goes to OpenAI (GPT-4o by default), which has access to four tools -- live gNMI queries via gnmic, historical metrics via Prometheus, YANG model search for path discovery, and a time utility for accurate Prometheus range queries. The LLM reasons about the question, calls tools (potentially chaining multiple calls), and produces a final Markdown answer rendered in the terminal.

The target environment is a 5-node Clos fabric lab: three leaves (leaf1-3) and two spines (spine1-2) running SR Linux.

## 2. System Architecture

### Data Flow

1. User types a natural language question in the REPL (`cli.py`, prompt_toolkit)
2. `Agent.chat()` appends the message to conversation history and sends it to OpenAI with the system prompt and tool definitions
3. The LLM reasons (produces `<reasoning>` tags), then returns tool calls
4. The agent executes each tool: gnmic subprocess, Prometheus HTTP request, or YANG index search
5. Tool results are appended to the conversation and sent back to the LLM
6. The LLM either calls more tools or produces a final text response (`finish_reason == "stop"`)
7. The final response is rendered as Rich Markdown in the terminal

### Component Diagram

```
+------------------+
|   User (REPL)    |
|   cli.py         |
|   prompt_toolkit |
+--------+---------+
         |
         | user_message (str)
         v
+--------+---------+       +-------------------+
|   Agent          |       |  OpenAI API       |
|   agent.py       +------>|  (GPT-4o)         |
|                  |<------+                   |
|  - chat loop     |       |  System prompt    |
|  - tool dispatch |       |  + tool defs      |
|  - history mgmt  |       |  from prompts.py  |
+----+----+----+---+       +-------------------+
     |    |    |
     |    |    +------------------------------------------+
     |    +-------------------------+                     |
     v                              v                     v
+----+----------+    +--------------+------+    +---------+------+
| gnmic_get     |    | prometheus_query    |    | yang_search    |
| tools/gnmic.py|    | tools/prometheus.py |    | tools/yang.py  |
+----+----------+    +--------------+------+    +---------+------+
     |                              |                     |
     v                              v                     v
  gnmic binary              Prometheus HTTP API     In-memory index
  (subprocess)              /api/v1/query{_range}   (pyang-parsed
  gNMI to device            :9090                    .yang files)
     |
     v
  SR Linux devices
  172.80.80.{11-13,21-22}
```

### Logging Side-Channel

Every interaction is logged by `TurnLogger` into a directory hierarchy under `./logs/`. Logging is a side-channel -- it never affects the agent loop and all writes are wrapped in try/except to avoid crashing the REPL.

## 3. Agent Loop (agent.py)

The `Agent` class holds the OpenAI client, conversation history (`self.messages: list[dict[str, Any]]`), tool references, and callback hooks.

### The `chat()` Loop

```python
async def chat(self, user_message: str) -> str:
    self.messages.append({"role": "user", "content": user_message})
    is_first_response = True

    while True:
        response = await self.client.chat.completions.create(...)
        choice = response.choices[0]

        # finish_reason == "stop" -> return final answer
        # finish_reason == "tool_calls" -> execute tools, loop again
```

The loop continues until the LLM produces a response with `finish_reason == "stop"`. On each iteration:

1. Send the full conversation history to OpenAI
2. If this is the first LLM response in the turn, extract `<reasoning>...</reasoning>` via regex and fire the `on_reasoning` callback
3. Serialize the assistant message (including any `tool_calls`) into conversation history
4. If `finish_reason == "stop"`, return the text content
5. If `tool_calls` are present, execute each one via `_execute_tool()`, append tool results to history, and loop

### Multi-Turn Tool Chaining

The LLM may call tools multiple times before producing a final answer. For example: `yang_search` to discover a path, then `gnmic_get` to query the device using that path. Each round-trip through the loop adds tool call and tool result messages to the conversation, giving the LLM full context for its next decision.

### Tool Result Truncation

Tool results are truncated to 30KB (`MAX_TOOL_RESULT_SIZE = 30_000`) before being placed into the LLM context. The full untruncated result is preserved in the turn logs.

### Callbacks

Three optional callbacks for UI integration:
- `on_tool_call(name, args)` -- fired before tool execution
- `on_tool_result(name, result_str)` -- fired after tool execution (receives the potentially truncated string)
- `on_reasoning(text)` -- fired when reasoning is extracted from the first LLM response

## 4. Tools

### gnmic_get (tools/gnmic.py)

Queries live device state via gNMI by shelling out to the `gnmic` binary.

- **Execution**: `asyncio.create_subprocess_exec` -- fully async, captures stdout and stderr
- **Target resolution**: Device name (e.g. `leaf1`) is resolved to an IP address via the `TOPOLOGY` dict in `config.py`
- **Command**: `gnmic -a {ip} -u admin -p NokiaSrl1! --skip-verify -e json_ietf get --path {path} --type {data_type}`
- **Timeout**: 15 seconds via `asyncio.wait_for`; on timeout the process is killed
- **Error handling**: Non-zero return code raises `RuntimeError` with stderr content
- **Returns**: Parsed JSON from stdout

### prometheus_query (tools/prometheus.py)

Queries Prometheus for historical telemetry metrics using `httpx.AsyncClient`.

- **Two endpoints**: The agent dispatches to `prometheus_query` (instant, `/api/v1/query`) or `prometheus_query_range` (range, `/api/v1/query_range`) based on whether `start` and `end` parameters are present
- **Timeout**: 15 seconds per request
- **Error handling**: Checks `status == "success"` in the Prometheus response; raises `RuntimeError` otherwise
- **Returns**: The `data` portion of the Prometheus JSON response (strips the outer envelope)

### yang_search (tools/yang.py)

Searches an in-memory index of SR Linux YANG models to discover valid gNMI paths.

**Index Construction** (`build_or_load_yang_index`):
- Uses pyang's `FileRepository` and `Context` with search paths derived by walking the YANG models directory for all directories containing `.yang` files
- All `.yang` files are loaded into the pyang context and validated
- A recursive tree walk (`_walk_node`) extracts `YangEntry` dataclass instances for every container, list, leaf, and leaf-list node: xpath, node_type, yang_type, description, module, keys
- Entries are deduplicated by xpath (augmentations can create duplicates across modules)

**Caching**:
- A SHA-256 hash is computed over all `.yang` file paths and their modification times
- The first 16 hex characters of this hash are used to name a pickle cache file in the cache directory
- If the cache file exists, the index loads from pickle (fast); otherwise it runs the full pyang parse (slow) and writes the cache

**Search Algorithm**:
- AND-logic keyword matching: all space-separated terms must appear in the lowercased concatenation of xpath + description
- Optional module name filter (substring match)
- Scoring: entries are ranked by (1) number of terms matching in the xpath itself (more = better), then (2) path depth measured by `/` count (shallower = better)
- Returns the top N results (default 20)

**Return format** (from agent.py): Each result is serialized with xpath, type, yang_type, description (truncated to 200 chars), module, and keys.

### get_current_time (agent.py inline)

Returns the current UTC time for constructing time-aware Prometheus queries.

- **Execution**: Inline in `_execute_tool` -- no external calls, returns immediately
- **Parameters**: None
- **Returns**: `{"utc_iso": "<ISO-8601 string>", "epoch": <unix timestamp>}`
- **Purpose**: Ensures the LLM uses accurate timestamps for Prometheus range query start/end values when the user references relative time periods ("last hour", "past 30 minutes")

## 5. LLM Integration (prompts.py)

### System Prompt

The system prompt (`SYSTEM_PROMPT`) provides the LLM with:

- **Lab topology**: The 5-node Clos fabric with IP addresses, interconnections (leaf-to-spine port mappings), client attachments, and routing design (eBGP underlay, iBGP overlay with EVPN/VXLAN)
- **Common gNMI paths**: A reference table of frequently-used paths (interfaces, BGP, route tables, system, platform) so the LLM can skip yang_search for well-known queries
- **Prometheus metric naming conventions**: How YANG paths map to Prometheus metric names (strip `/state/`, replace `/` and `-` with `_`), plus key metrics with their label schemas
- **Reasoning instruction**: Directs the LLM to wrap its reasoning in `<reasoning>...</reasoning>` tags before calling tools
- **Tool selection decision framework**: A numbered guide for choosing the right tool based on the question type (current state vs. historical trends vs. path discovery), including anti-patterns to avoid

### Tool Definitions

Four OpenAI function calling tool definitions:

| Tool | Required Params | Optional Params |
|------|----------------|-----------------|
| `gnmic_get` | `target` (enum of 5 devices), `path` | `data_type` (ALL/CONFIG/STATE/OPERATIONAL) |
| `prometheus_query` | `query` (PromQL) | `time`, `start`, `end`, `step` |
| `yang_search` | `keyword` | `module_filter`, `max_results` |
| `get_current_time` | (none) | (none) |

The LLM is guided to chain tools: `yang_search` to find the right path, then `gnmic_get` or `prometheus_query` to fetch the data.

## 6. Reasoning

The system prompt instructs the LLM to produce `<reasoning>...</reasoning>` tags before its first tool call in each user turn. This makes the agent's thought process visible to the user.

**Extraction logic** (in `agent.py`):
- Only on the first LLM response per turn, tracked by the `is_first_response` flag
- Extracted via `re.search(r"<reasoning>(.*?)</reasoning>", msg.content, re.DOTALL)`
- Displayed in the REPL via the `on_reasoning` callback, which renders each line as dim `>>>` prefixed text
- Logged as `llm_reasoning.json` in the turn directory

After the first response, `is_first_response` is set to `False`, so subsequent LLM responses in the same turn (after receiving tool results) do not trigger reasoning extraction.

## 7. Structured Turn Logging (turn_logging.py)

### Directory Hierarchy

```
./logs/
  session_2026-03-29T14-30-00/
    session_summary.json
    turn_001/
      00_user_message.json
      01_llm_response.json
      02_llm_reasoning.json
      03_tool_call_yang_search.json
      04_tool_result_yang_search.json
      05_llm_response.json
      06_tool_call_gnmic_get.json
      07_tool_result_gnmic_get.json
      08_llm_response_final.json
    turn_002/
      ...
```

### TurnLogger Class

- **Initialization**: Takes a session directory path. Creates it on construction; tracks turn count, file sequence, and cumulative statistics.
- **Turn lifecycle**: `start_turn()` increments the turn counter, resets the file sequence to 0, and creates the turn directory.
- **File naming**: `{seq:02d}_{type_hint}.json` -- auto-incrementing sequence ensures chronological ordering within a turn.

### File Types

| File | Contents |
|------|----------|
| `user_message` | Raw user input |
| `llm_response` | LLM response with tool calls (intermediate) |
| `llm_response_final` | LLM final text response (`finish_reason == "stop"`) |
| `llm_reasoning` | Extracted reasoning text |
| `tool_call_{name}` | Tool call ID, name, and arguments |
| `tool_result_{name}` | Full (untruncated) result, duration in ms, success/error status |

### Session Summary

`session_summary.json` is written/updated at the end of each turn. Contains: session ID, model name, turn count, total tool calls, per-tool call counts, cumulative token usage (prompt/completion/total), and error count.

### Reliability

All file writes are synchronous and wrapped in `try/except` with a printed warning. The logger never raises exceptions that would crash the REPL.

## 8. Configuration (config.py)

### Config Dataclass

| Field | Default | Env Var |
|-------|---------|---------|
| `openai_api_key` | (required) | `OPENAI_API_KEY` |
| `openai_model` | `gpt-4o` | `OPENAI_MODEL` |
| `prometheus_url` | `http://localhost:9090` | `PROMETHEUS_URL` |
| `yang_models_dir` | `./srlinux-yang-models` | `YANG_MODELS_DIR` |
| `yang_cache_dir` | `.cache` | `YANG_CACHE_DIR` |
| `logs_dir` | `./logs` | `SRL_EXPLORER_LOGS_DIR` |

Environment variables are loaded via `python-dotenv` (`load_dotenv()` at module import time).

### Topology and Credentials

Hardcoded in `config.py`:

```python
TOPOLOGY = {
    "leaf1":  {"address": "172.80.80.11", "role": "leaf"},
    "leaf2":  {"address": "172.80.80.12", "role": "leaf"},
    "leaf3":  {"address": "172.80.80.13", "role": "leaf"},
    "spine1": {"address": "172.80.80.21", "role": "spine"},
    "spine2": {"address": "172.80.80.22", "role": "spine"},
}

CREDENTIALS = {"username": "admin", "password": "NokiaSrl1!"}
```

## 9. Key Design Decisions

**Shell out to gnmic** rather than using a Python gNMI library. This avoids heavy gRPC/protobuf dependencies, leverages the same CLI that network engineers already use, and means the tool's behavior is identical to what a human would get running gnmic manually. Also demonstrates how to make a CLI tool an LLM/AI tool

**pyang for YANG parsing**. pyang correctly resolves imports, augmentations, and deviations across a large set of YANG files, producing a complete resolved module tree. This is essential for SR Linux, where the YANG model is split across hundreds of files with extensive cross-module augmentation.  Also shows how to make a library tool and AI tool.

**Directory-per-turn logging**. Each turn gets its own directory with numbered JSON files. This makes logs human-inspectable with basic shell tools (ls, cat, jq), easy to diff between sessions, and avoids any database dependency.

**Async throughout**. Both gnmic subprocess execution and Prometheus HTTP calls are I/O-bound. Using asyncio keeps the REPL responsive -- the event loop is free to handle user interrupts while waiting for tool results.

**OpenAI function calling**. Structured tool dispatch with typed parameters and enum constraints. The API natively supports multi-turn tool chaining without requiring custom output parsing -- the LLM returns structured tool call objects, and the agent feeds results back as tool-role messages.

**Tool result truncation with full logging**. Large gNMI responses (e.g., full routing tables) are truncated to 30KB for the LLM context to stay within token limits, but the complete result is preserved in the turn logs for debugging and auditing.
