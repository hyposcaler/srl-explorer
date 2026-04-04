from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCall,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)

from srl_explorer.config import Config
from srl_explorer.turn_logging import TurnLogger
from srl_explorer.prompts import SYSTEM_PROMPT, TOOLS
from srl_explorer.tools.gnmic import gnmic_get
from srl_explorer.tools.prometheus import prometheus_query, prometheus_query_range
from srl_explorer.tools.yang import YangIndex

MAX_TOOL_RESULT_SIZE = 30_000
# Safety guard: cap the agent loop to prevent runaway API costs.
# With GPT-4o pricing, an unbounded loop can get expensive fast.
MAX_AGENT_ITERATIONS = 25


class Agent:
    def __init__(
        self,
        config: Config,
        yang_index: YangIndex,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        logger: TurnLogger | None = None,
    ) -> None:
        self.client = AsyncOpenAI(api_key=config.openai_api_key, max_retries=3)
        self.model = config.openai_model
        self.config = config
        self.yang_index = yang_index
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_reasoning = on_reasoning
        self.logger = logger
        # Conversation transcript sent to the LLM on every call. Uses four roles:
        # system (instructions), user (human input), assistant (LLM replies),
        # tool (tool execution results). The full transcript is how the model
        # maintains context across multi-turn tool chains.
        self.messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.tools: list[ChatCompletionToolParam] = TOOLS

    def clear_history(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def history_token_estimate(self) -> int:
        # Rough estimate — 1 token is roughly 4 chars for English text.
        # Good enough for threshold warnings without adding tiktoken as a dependency.
        total_chars = 0
        for msg in self.messages:
            content = msg.get("content")
            if content:
                total_chars += len(content)
            for tc in msg.get("tool_calls", []):
                total_chars += len(tc["function"]["arguments"])
        return total_chars // 4

    def context_usage_pct(self) -> float:
        return self.history_token_estimate() / self.config.context_window

    def _extract_reasoning(self, content: str | None) -> str | None:
        """Strip <reasoning> tags from content, firing callbacks and logging."""
        if not content:
            return content
        m = re.search(r"<reasoning>(.*?)</reasoning>", content, re.DOTALL)
        if not m:
            return content
        reasoning_text = m.group(1).strip()
        if self.on_reasoning:
            self.on_reasoning(reasoning_text)
        if self.logger:
            self.logger.log_reasoning(reasoning_text)
        return re.sub(
            r"<reasoning>.*?</reasoning>\s*", "", content, flags=re.DOTALL
        ).strip() or None

    def _build_assistant_message(
        self, content: str | None, tool_calls: list[ChatCompletionMessageToolCall]
    ) -> ChatCompletionAssistantMessageParam:
        """Build a plain dict for conversation history from an LLM response."""
        # OpenAI response objects aren't JSON-serializable, so we build
        # a plain dict for the conversation history.
        msg_dict: ChatCompletionAssistantMessageParam = {
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
        return msg_dict

    async def _run_tool_call(self, tc: ChatCompletionMessageToolCall) -> ChatCompletionToolMessageParam:
        """Execute one tool call, log the full result, and return the tool message dict."""
        name = tc.function.name

        result = None
        error = None
        t0 = time.monotonic()
        try:
            args = json.loads(tc.function.arguments)

            if self.on_tool_call:
                self.on_tool_call(name, args)

            if self.logger:
                self.logger.log_tool_call(tc.id, name, args)

            result = await self._execute_tool(name, args)
            result_str = json.dumps(result, default=str)
        except Exception as e:
            error = str(e)
            result_str = json.dumps({"error": error})

        duration_ms = int((time.monotonic() - t0) * 1000)

        if self.logger:
            self.logger.log_tool_result(
                tc.id, name, result, duration_ms, error
            )

        # Truncate large results for the LLM context
        if len(result_str) > MAX_TOOL_RESULT_SIZE:
            result_str = (
                result_str[:MAX_TOOL_RESULT_SIZE]
                + "\n... [truncated, result too large]"
            )

        # Callback receives the (possibly truncated) result string
        if self.on_tool_result:
            self.on_tool_result(name, result_str)

        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result_str,
        }

    async def chat(self, user_message: str) -> str:
        """Run one user turn through the agent loop.

        Sends the message to the LLM, executes any tool calls, and loops
        until the model produces a final response. Returns the response text.
        """
        if self.logger:
            self.logger.start_turn()
            self.logger.log_user_message(user_message)

        self.messages.append({"role": "user", "content": user_message})
        # Reasoning is only extracted from the first LLM response in a turn —
        # that's where the planning happens; subsequent responses are execution.
        is_first_response = True

        for _iteration in range(MAX_AGENT_ITERATIONS):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tools,
            )
            choice = response.choices[0]
            msg = choice.message

            if self.logger:
                self.logger.log_llm_response(
                    msg, response.usage, choice.finish_reason, self.model
                )

            content = msg.content
            if is_first_response and content:
                content = self._extract_reasoning(content)
                is_first_response = False

            tool_calls = [
                tc for tc in (msg.tool_calls or [])
                if isinstance(tc, ChatCompletionMessageToolCall)
            ]
            self.messages.append(self._build_assistant_message(content, tool_calls))

            if choice.finish_reason == "stop":
                if self.logger:
                    self.logger.update_session_summary()
                return content or ""

            if tool_calls:
                for tc in tool_calls:
                    self.messages.append(await self._run_tool_call(tc))
                continue

            # Unexpected finish reason — return whatever content we have
            if self.logger:
                self.logger.update_session_summary()
            return content or ""

        raise RuntimeError(
            f"Agent exceeded {MAX_AGENT_ITERATIONS} iterations without finishing. "
            "This is a safety limit to prevent runaway API costs."
        )

    async def _execute_tool(self, name: str, args: dict) -> Any:
        """Dispatch a tool call by name. The explicit if-chain is intentional —
        a registry pattern would reduce repetition but hides the mapping from
        readers. For a teaching codebase with few tools, this is easier to follow.
        See docs/adding-a-tool.md for how to extend it.
        """
        if name == "gnmic_get":
            return await gnmic_get(
                self.config,
                target=args["target"],
                path=args["path"],
                data_type=args.get("data_type", "ALL"),
            )

        if name == "prometheus_query":
            if args.get("start") and args.get("end"):
                return await prometheus_query_range(
                    self.config,
                    query=args["query"],
                    start=args["start"],
                    end=args["end"],
                    step=args.get("step", "15s"),
                )
            return await prometheus_query(
                self.config,
                query=args["query"],
                time=args.get("time"),
            )

        if name == "yang_search":
            results = self.yang_index.search(
                keyword=args["keyword"],
                module_filter=args.get("module_filter"),
                max_results=args.get("max_results", 20),
            )
            return [
                {
                    "xpath": r.xpath,
                    "type": r.node_type,
                    "yang_type": r.yang_type,
                    "description": r.description[:200] if r.description else "",
                    "module": r.module,
                    "keys": r.keys,
                }
                for r in results
            ]

        if name == "get_current_time":
            now = datetime.now(timezone.utc)
            return {
                "utc_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "epoch": int(now.timestamp()),
            }

        raise ValueError(f"Unknown tool: {name}")
