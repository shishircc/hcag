"""Sample driver for the HCAG runtime agent.

Two modes:
  # One-shot: pass the question as an argument
  python examples/run_agent.py --config ./examples/agent.toml "How do refunds work?"

  # Interactive REPL: no question argument
  python examples/run_agent.py --config ./examples/agent.toml

Prerequisites:
  1. Build the KB:      hcag preprocess ./my-kb && hcag aggregate ./my-kb
  2. Set credentials:   export ANTHROPIC_API_KEY=...   (or AWS_* for Bedrock)
  3. Edit agent.toml:   set kb_root to your KB path
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hcag.config import AgentConfig, LLMConfig, load_agent_config
from hcag.runtime import AgentRuntime


def _build_config(config_path: Path | None, kb_root: str | None) -> AgentConfig:
    if config_path is not None:
        if not config_path.is_file():
            print(f"config not found: {config_path}", file=sys.stderr)
            sys.exit(2)
        cfg = load_agent_config(config_path)
        if kb_root:
            cfg.kb_root = kb_root
        return cfg

    # No config file — build a minimal one from flags + defaults
    if not kb_root:
        print("either --config or --kb-root is required", file=sys.stderr)
        sys.exit(2)
    return AgentConfig(
        kb_root=kb_root,
        llm=LLMConfig(),  # anthropic + claude-3-5-haiku-20241022 by default
    )


def _run_once(agent: AgentRuntime, question: str) -> None:
    reply = agent.run_turn(question)
    print(reply)


def _run_repl(agent: AgentRuntime) -> None:
    agent.bootstrap()
    print("HCAG agent ready. Ctrl-D or Ctrl-C to exit.\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user:
            continue
        try:
            reply = agent.run_turn(user)
        except KeyboardInterrupt:
            print("\n(interrupted)")
            continue
        print(f"agent> {reply}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the HCAG runtime agent.")
    parser.add_argument("question", nargs="?", help="Question to ask. If omitted, enter interactive REPL.")
    parser.add_argument("--config", type=Path, help="Path to agent.toml.")
    parser.add_argument("--kb-root", help="KB root (overrides config or replaces it if --config not given).")
    args = parser.parse_args(argv)

    cfg = _build_config(args.config, args.kb_root)
    agent = AgentRuntime(cfg=cfg)

    if args.question:
        _run_once(agent, args.question)
    else:
        _run_repl(agent)


if __name__ == "__main__":
    main()
