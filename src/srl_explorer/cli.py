from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown

from srl_explorer.agent import Agent
from srl_explorer.config import get_config
from srl_explorer.turn_logging import TurnLogger
from srl_explorer.tools.yang import build_or_load_yang_index

console = Console()

HELP_TEXT = """\
## srl-explorer

An AI-powered CLI for querying Nokia SR Linux telemetry.

### Commands
- **/help** — Show this help
- **/clear** — Clear conversation history
- **/quit** — Exit

### Tips
- Ask natural language questions about your network
- The agent can query live devices (gnmic), historical metrics (Prometheus), or search YANG models
- You can explicitly request a tool: "use gnmic to show leaf1 interfaces"
- The agent chains tools automatically: discovers YANG paths, then queries devices

### Lab Topology
leaf1, leaf2, leaf3 (leaves) — spine1, spine2 (spines)
"""


def _on_reasoning(text: str) -> None:
    for line in text.strip().splitlines():
        console.print(f"  [dim]>>> {line}[/dim]")


def _on_tool_call(name: str, args: dict) -> None:
    args_str = ", ".join(f"{k}={v}" for k, v in args.items())
    console.print(f"  [dim]>>> {name}({args_str})[/dim]")


# Raw tool results are intentionally not echoed -- they're often large
# JSON payloads that would clutter the REPL. See logs/ for full output.
def _on_tool_result(name: str, result: str) -> None:
    pass


async def _run() -> None:
    config = get_config()

    with console.status("[bold green]Building YANG index..."):
        yang_index = build_or_load_yang_index(
            config.yang_models_dir, config.yang_cache_dir
        )

    session_id = "session_" + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    session_dir = config.logs_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    logger = TurnLogger(session_dir)

    console.print(
        f"[bold]srl-explorer[/bold] — {len(yang_index)} YANG paths indexed. "
        f"Type [bold]/help[/bold] for commands.\n"
    )

    agent = Agent(
        config,
        yang_index,
        on_tool_call=_on_tool_call,
        on_tool_result=_on_tool_result,
        on_reasoning=_on_reasoning,
        logger=logger,
    )

    session: PromptSession[str] = PromptSession(
        history=FileHistory(".srl_explorer_history"),
        auto_suggest=AutoSuggestFromHistory(),
    )

    _warned_75 = False

    while True:
        try:
            user_input = await session.prompt_async("srl> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        text = user_input.strip()
        if not text:
            continue

        if text == "/quit":
            console.print("Bye!")
            break
        elif text == "/clear":
            agent.clear_history()
            _warned_75 = False
            console.print("[green]Conversation cleared.[/green]")
            continue
        elif text == "/help":
            console.print(Markdown(HELP_TEXT))
            continue

        try:
            response = await agent.chat(text)
            console.print()
            console.print(Markdown(response))
            console.print()

            usage = agent.context_usage_pct()
            est = agent.history_token_estimate()
            window = agent.config.context_window
            if usage >= 0.90:
                console.print(
                    f"[yellow]Context is ~90% full (~{est:,} of {window:,} tokens est). "
                    f"Response quality may degrade. Use /clear to reset.[/yellow]"
                )
            elif usage >= 0.75 and not _warned_75:
                console.print(
                    f"[dim]Context is ~75% full (~{est:,} of {window:,} tokens est). "
                    f"Consider /clear if you're starting a new topic.[/dim]"
                )
                _warned_75 = True
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]\n")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
