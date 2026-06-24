"""CLI entry point — run the agent directly from terminal for quick testing."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agri_agent.agent.react_agent import run_agent
from agri_agent.config.loader import list_agent_configs, load_agent_config


def main():
    parser = argparse.ArgumentParser(description="AgriScience Agent CLI")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Run an agent")
    run_p.add_argument("agent", help="Agent config name (e.g. react-agent)")
    run_p.add_argument("message", help="User message")
    run_p.add_argument("--thread-id", help="Thread ID for conversation continuity")

    sub.add_parser("list", help="List available agent configs")

    args = parser.parse_args()

    if args.cmd == "list":
        configs = list_agent_configs()
        if not configs:
            print("No configs found in agents/configs/")
            return
        for c in configs:
            print(f"  {c.name:30s}  v{c.version}  [{c.model.provider}/{c.model.name}]")
            print(f"    Tools: {', '.join(c.enabled_tools())}")
        return

    if args.cmd == "run":
        try:
            cfg = load_agent_config(args.agent)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)

        print(f"Running agent: {cfg.name}\n{'─' * 60}")
        result = run_agent(cfg, args.message, thread_id=args.thread_id)

        print(f"\n{'─' * 60}")
        print(f"Output:\n{result['output']}")
        if result["tool_calls"]:
            print(f"\nTool calls: {json.dumps(result['tool_calls'], indent=2)}")
        print(
            f"\nTokens — in: {result['input_tokens']}  out: {result['output_tokens']}  "
            f"time: {result['elapsed_seconds']}s"
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
