from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from openai import AsyncOpenAI

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
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def clear_history(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    async def chat(self, user_message: str) -> str:
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
                tools=TOOLS,
            )
            choice = response.choices[0]
            msg = choice.message

            if self.logger:
                self.logger.log_llm_response(
                    msg, response.usage, choice.finish_reason, self.model
                )

            # Extract reasoning from first LLM response and strip tags
            content = msg.content
            if is_first_response and content:
                m = re.search(
                    r"<reasoning>(.*?)</reasoning>", content, re.DOTALL
                )
                if m:
                    reasoning_text = m.group(1).strip()
                    if self.on_reasoning:
                        self.on_reasoning(reasoning_text)
                    if self.logger:
                        self.logger.log_reasoning(reasoning_text)
                    content = re.sub(
                        r"<reasoning>.*?</reasoning>\s*", "", content, flags=re.DOTALL
                    ).strip() or None
                is_first_response = False

            # OpenAI response objects aren't JSON-serializable, so we build
            # a plain dict for the conversation history.
            msg_dict: dict[str, Any] = {"role": "assistant", "content": content}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            self.messages.append(msg_dict)

            if choice.finish_reason == "stop":
                if self.logger:
                    self.logger.update_session_summary()
                return content or ""

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)

                    if self.on_tool_call:
                        self.on_tool_call(name, args)

                    if self.logger:
                        self.logger.log_tool_call(tc.id, name, args)

                    t0 = time.monotonic()
                    result = None
                    error = None
                    try:
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

                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        }
                    )
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
