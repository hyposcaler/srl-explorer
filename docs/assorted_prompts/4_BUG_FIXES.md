# srl-explorer Round 2 Changes

Two changes to implement.

## 1. Move tool-call argument parsing inside the error-handling path (agent.py)

Currently, `json.loads(tc.function.arguments)` happens before the try/except block. If the LLM emits malformed JSON in the arguments field, the entire turn crashes before logging and before the model gets a chance to recover. This is a real bug — models do occasionally produce invalid JSON in tool call arguments.

Move the argument parsing inside the existing try/except so that a malformed payload is surfaced back to the LLM as a structured tool error, giving it a chance to retry.

The restructured flow for the tool call loop body should be:

1. Extract `name = tc.function.name` before the try block
2. Inside the try block: parse arguments with `json.loads`, fire `on_tool_call` callback, log the tool call, execute the tool, capture timing
3. In the except block: capture the error string, build the error result_str, set duration_ms to 0
4. After the try/except (unchanged): log the tool result, apply truncation, fire `on_tool_result` callback, append the tool message to conversation history

The key behavioral change: a `json.JSONDecodeError` from malformed arguments now produces a tool error message in the conversation instead of crashing the turn. The model sees the error and can retry.

### Test

Add a test called `test_malformed_tool_arguments` in tests/test_agent.py. It should:

- Mock a tool call where `tc.function.arguments` is invalid JSON (e.g. the string `"{bad json"`)
- Use `ChatCompletionMessageToolCall` and `Function` from the openai types, same as the existing `_mock_tool_call` helper, but pass the raw bad string directly to Function(arguments=...)
- Return a final "ok" response on the second LLM call
- Assert that `chat()` does NOT raise an exception
- Assert that a tool message with an error was appended to conversation history
- Assert that the agent returned the final response content

## 2. Add conversation history size tracking with tiered REPL warnings

The conversation history is unbounded. Over long REPL sessions, token cost and latency increase silently. Rather than implementing a rolling window (which adds complexity that obscures the teaching value), make the problem visible so users learn about context window costs.

### Config changes (config.py)

Add a `context_window: int` field to the Config dataclass with default `128_000` (GPT-4o's context window in tokens). Source it from env var `CONTEXT_WINDOW` in `get_config()`, converting to int.

Add `CONTEXT_WINDOW` to `.env.example` with a comment: "Max context window in tokens — should match your model (default: 128000 for GPT-4o)"

### Agent changes (agent.py)

Add a method `history_token_estimate(self) -> int` that sums the character length of all message content fields in `self.messages` and divides by 4. Use integer division. For messages where content is None, count as 0. For tool_calls in a message, also count the serialized arguments length. Add a comment explaining the heuristic: "Rough estimate — 1 token is roughly 4 chars for English text. Good enough for threshold warnings without adding tiktoken as a dependency."

Add a method `context_usage_pct(self) -> float` that returns `self.history_token_estimate() / self.config.context_window`.

### CLI changes (cli.py)

Add a `_warned_75` boolean flag initialized to False before the REPL loop.

After each successful `agent.chat()` call and response display, check `agent.context_usage_pct()`:

- If >= 0.90: always print `"[yellow]Context is ~90% full (~{est:,} of {window:,} tokens est). Response quality may degrade. Use /clear to reset.[/yellow]"` — this prints every turn because the user needs to act.
- Elif >= 0.75 and not `_warned_75`: print `"[dim]Context is ~75% full (~{est:,} of {window:,} tokens est). Consider /clear if you're starting a new topic.[/dim]"` and set `_warned_75 = True`. Only fires once.

Reset `_warned_75 = False` when the user runs `/clear`.

The `est` value comes from `agent.history_token_estimate()` and `window` from `agent.config.context_window`.

### What NOT to do

Do NOT implement a rolling window, conversation pruning, or automatic history management. Do NOT add tiktoken as a dependency. The point is to make the cost visible and let the user decide.