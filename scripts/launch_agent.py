#!/usr/bin/env python3
"""
Fundly Agent Launcher

Interactive CLI that drives a conversation with Claude to gather agent
requirements, recommends tools from the platform registry, generates the
YAML manifest, and optionally opens a GitHub PR for CI/CD deployment.

The launcher creates the YAML manifest ONLY.
Tool implementations (API wrappers) are written by developers separately
and registered in src/agri_agent/agent/tools/__init__.py.

Usage:
    make launch-agent
    make launch-agent MODEL=claude-opus-4-8
    make launch-agent NO_GIT=1
    uv run python scripts/launch_agent.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── Load .env ──────────────────────────────────────────────────────────────────

def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


# ── Terminal helpers ───────────────────────────────────────────────────────────

BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
W      = 72


def _hr(char: str = "─") -> None:
    print(f"{DIM}{char * W}{RESET}")


def _banner(text: str) -> None:
    print()
    _hr("═")
    pad = " " * ((W - len(text)) // 2)
    print(f"{BOLD}{CYAN}{pad}{text}{RESET}")
    _hr("═")


def _section(text: str) -> None:
    print(f"\n{BOLD}{YELLOW}▸ {text}{RESET}")


def _info(text: str) -> None:
    print(f"  {CYAN}ℹ  {text}{RESET}")


def _success(text: str) -> None:
    print(f"  {GREEN}✓  {text}{RESET}")


def _warn(text: str) -> None:
    print(f"  {YELLOW}⚠  {text}{RESET}")


def _error(text: str) -> None:
    print(f"  {RED}✗  {text}{RESET}", file=sys.stderr)


def _file_block(label: str, content: str) -> None:
    print(f"\n  {BOLD}{BLUE}📄 {label}{RESET}")
    _hr("·")
    for line in content.splitlines():
        print(f"  {line}")
    _hr("·")


def _ask(prompt: str = "") -> str:
    try:
        raw = input(f"\n{BOLD}You:{RESET} {prompt}")
        return raw.strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{DIM}Exiting.{RESET}")
        sys.exit(0)


def _confirm(question: str) -> bool:
    return _ask(f"{question} [y/n]: ").lower() in ("y", "yes")


# ── Tool discovery ─────────────────────────────────────────────────────────────

def _get_available_tools() -> list[dict]:
    """Return [{name, description}] from the live tool registry."""
    try:
        from agri_agent.agent.tools import list_tools_with_descriptions
        return list_tools_with_descriptions()
    except Exception as exc:
        _warn(f"Could not load tool registry: {exc}")
        return []


def _format_tool_list(tools: list[dict]) -> str:
    if not tools:
        return "  (no tools registered)"
    lines = []
    for t in tools:
        desc = (t["description"] or "").split("\n")[0][:80]
        lines.append(f"  • {t['name']:<30} {desc}")
    return "\n".join(lines)


# ── LLM client ────────────────────────────────────────────────────────────────

def _make_client(model: str) -> tuple[Any, str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        _error("ANTHROPIC_API_KEY not set. Add it to .env and retry.")
        sys.exit(1)
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key), model
    except ImportError:
        _error("anthropic package not found. Run: uv sync")
        sys.exit(1)


def _chat(client: Any, model: str, messages: list[dict], system: str) -> str:
    """Send messages, stream the response, return full text."""
    print(f"\n{BOLD}{CYAN}Launcher:{RESET} ", end="", flush=True)
    full = ""
    with client.messages.stream(
        model=model,
        max_tokens=8192,
        system=system,
        messages=messages,
    ) as stream:
        for chunk in stream.text_stream:
            print(chunk, end="", flush=True)
            full += chunk
    print()
    return full


def _extract_json(text: str) -> dict | None:
    """Extract the first valid JSON object from text (fenced or bare)."""
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Brace-matching scan
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


# ── YAML template defaults (for the LLM to follow) ────────────────────────────

_YAML_EXAMPLE = """
agent:
  name: support-agent
  description: >
    Handles inbound customer support queries via email and chat.
  version: "1.0.0"

  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.2
    max_tokens: 4096
    max_cost_usd: 0.50

  system_prompt: |
    You are a customer support specialist for Fundly.
    Help users with their queries clearly and professionally.
    Escalate anything involving financial disputes to a human agent.

  tools:
    - name: calculator
      enabled: true
    - name: web_search
      enabled: true
      config:
        max_results: 3

  guardrails:
    max_iterations: 15
    timeout_seconds: 120
    blocked_patterns:
      - "ignore previous instructions"
      - "jailbreak"

  observability:
    langsmith_tracing: true
    log_inputs: true
    log_outputs: true
    log_tool_calls: true
""".strip()


# ── System prompts ─────────────────────────────────────────────────────────────

def _make_discovery_system(tools: list[dict]) -> str:
    tool_block = _format_tool_list(tools)
    tool_names = [t["name"] for t in tools]

    return f"""
You are the Fundly Agent Launcher — a tool that helps developers create new AI agents
for the Fundly platform through a focused conversation. Ask 1-2 questions per turn.

You need to gather:
1. A short hyphenated agent name (e.g. "support-agent", "order-router")
2. What the agent does and who uses it (purpose + target users)
3. Which tools to enable — recommend from the registry below based on the description
4. Model parameters: temperature (default 0.2), max_tokens (default 4096), max_cost_usd
5. A concise system prompt (3-6 sentences) that defines the agent's role and behaviour

Available tools in the registry:
{tool_block}

Important rules:
- ONLY recommend tools that exist in the registry above: {json.dumps(tool_names)}
- Do NOT suggest creating new tools — that is a separate developer task
- Observability and guardrails use platform defaults unless the user explicitly changes them
- Default model: anthropic / claude-sonnet-4-6
- Always include "ignore previous instructions" in blocked_patterns

When you have everything, output ONLY this JSON block with nothing after it:

```json
{{
  "ready": true,
  "agent_name": "my-agent",
  "description": "One sentence description.",
  "target_users": "Who uses this agent.",
  "model": {{
    "provider": "anthropic",
    "name": "claude-sonnet-4-6",
    "temperature": 0.2,
    "max_tokens": 4096,
    "max_cost_usd": 0.50
  }},
  "system_prompt": "You are a ...",
  "tools": ["calculator", "web_search"],
  "guardrails": {{
    "max_iterations": 15,
    "timeout_seconds": 120,
    "blocked_patterns": ["ignore previous instructions"]
  }}
}}
```
""".strip()


GENERATION_SYSTEM = f"""
You are a YAML generator for the Fundly agent platform.

Given a spec JSON, output ONLY a valid YAML manifest for the agent.
No explanation, no preamble, no fences — just the raw YAML content.

Follow this exact structure (keys, indentation, quoting):

{_YAML_EXAMPLE}

Rules:
- agent.name: the exact value from the spec (lowercase, hyphens only)
- system_prompt: use "system_prompt: |" with 4-space indented body
- tools: only tools that appear in the spec's "tools" list
- blocked_patterns: must be valid Python regex strings
- observability: always include all four flags set to true
- version: always "1.0.0"
""".strip()


# ── Phase 1: Discovery ─────────────────────────────────────────────────────────

def run_discovery(client: Any, model: str, tools: list[dict]) -> dict:
    """Multi-turn conversation until Claude produces a complete agent spec."""
    _section("Discovery")
    _info("I'll ask a few questions to understand the agent you want to build.")
    _info("Type 'quit' to exit.\n")

    print(
        f"\n{BOLD}{CYAN}Launcher:{RESET} "
        "Hi! Let's define your new agent.\n"
        "         Start by telling me: what should this agent do, and who will use it?"
    )

    system = _make_discovery_system(tools)
    messages: list[dict] = []

    while True:
        user_text = _ask()
        if user_text.lower() in ("quit", "exit", "q"):
            sys.exit(0)

        messages.append({"role": "user", "content": user_text})
        response = _chat(client, model, messages, system)
        messages.append({"role": "assistant", "content": response})

        spec = _extract_json(response)
        if spec and spec.get("ready"):
            return spec


# ── Phase 2: Generate YAML ─────────────────────────────────────────────────────

def run_generation(client: Any, model: str, spec: dict) -> str:
    """Generate YAML manifest from the spec. Returns raw YAML string."""
    _section("Generating YAML manifest...")

    prompt = (
        "Generate the YAML manifest for this agent specification:\n\n"
        f"```json\n{json.dumps(spec, indent=2)}\n```\n\n"
        "Output only the raw YAML content — no fences, no explanation."
    )
    messages = [{"role": "user", "content": prompt}]
    response = _chat(client, model, messages, GENERATION_SYSTEM)

    # Strip any accidental fences the model might add
    yaml_content = re.sub(r"^```(?:yaml)?\s*", "", response.strip())
    yaml_content = re.sub(r"\s*```$", "", yaml_content)
    return yaml_content.strip()


# ── Phase 3: Review loop ───────────────────────────────────────────────────────

def run_review(client: Any, model: str, spec: dict, yaml_content: str) -> str:
    """Show YAML to the user; iterate until accepted."""
    while True:
        _section("Review YAML manifest")

        yaml_fname = spec["agent_name"].replace("-", "_") + ".yaml"
        _file_block(f"agents/configs/{yaml_fname}", yaml_content)

        print(f"\n  {DIM}Agent will be registered as{RESET} {BOLD}inactive{RESET} — "
              "activate it from the dashboard after deployment.")

        print(f"\n{BOLD}What would you like to do?{RESET}")
        print("  [a]  Accept — write file and continue")
        print("  [m]  Modify — describe changes to regenerate")
        print("  [q]  Quit without saving")

        choice = _ask("Choice [a/m/q]: ").lower()

        if choice in ("a", "accept", ""):
            return yaml_content

        if choice in ("q", "quit"):
            print(f"\n{DIM}No files written. Goodbye.{RESET}")
            sys.exit(0)

        if choice in ("m", "modify", "r"):
            instruction = _ask("Describe what to change: ")
            if not instruction:
                continue

            _section("Regenerating...")
            prompt = (
                f"Original spec:\n```json\n{json.dumps(spec, indent=2)}\n```\n\n"
                f"Current YAML:\n```yaml\n{yaml_content}\n```\n\n"
                f"Modification: {instruction}\n\n"
                "Output only the updated raw YAML — no fences, no explanation."
            )
            messages = [{"role": "user", "content": prompt}]
            response = _chat(client, model, messages, GENERATION_SYSTEM)
            updated = re.sub(r"^```(?:yaml)?\s*", "", response.strip())
            updated = re.sub(r"\s*```$", "", updated).strip()
            if updated:
                yaml_content = updated
            else:
                _warn("Could not parse updated YAML — keeping current version.")
        else:
            _warn("Enter 'a', 'm', or 'q'.")


# ── Phase 4: Write file ────────────────────────────────────────────────────────

def write_yaml(spec: dict, yaml_content: str) -> Path:
    """Write the YAML manifest to agents/configs/. Returns the written path."""
    yaml_fname = spec["agent_name"].replace("-", "_") + ".yaml"
    yaml_path = ROOT / "agents" / "configs" / yaml_fname

    if yaml_path.exists():
        if not _confirm(f"  {yaml_path.relative_to(ROOT)} already exists. Overwrite?"):
            _info("Skipping file write.")
            return yaml_path

    yaml_path.write_text(yaml_content + "\n")
    _success(f"Written: {yaml_path.relative_to(ROOT)}")
    return yaml_path


# ── Phase 5: Git + PR ──────────────────────────────────────────────────────────

def _run_cmd(args: list[str]) -> tuple[int, str, str]:
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def git_and_pr(spec: dict, yaml_path: Path, no_git: bool) -> None:
    agent_name = spec["agent_name"]
    rel = str(yaml_path.relative_to(ROOT))

    if no_git:
        _section("Git step skipped (--no-git)")
        _info("When ready:")
        _info(f"  git checkout -b agent/add-{agent_name}")
        _info(f"  git add {rel}")
        _info(f"  git commit -m 'Add {agent_name} agent manifest'")
        _info("  git push -u origin HEAD")
        return

    if not _confirm("\nCreate a git branch and open a PR?"):
        _section("Git step skipped")
        _info(f"  git checkout -b agent/add-{agent_name}")
        _info(f"  git add {rel}")
        _info(f"  git commit -m 'Add {agent_name} agent manifest'")
        _info("  git push -u origin HEAD")
        return

    _section("Git operations")
    branch = f"agent/add-{agent_name}"

    code, _, err = _run_cmd(["git", "checkout", "-b", branch])
    if code != 0:
        _warn(f"Branch may exist, switching: {err}")
        _run_cmd(["git", "checkout", branch])
    _success(f"Branch: {branch}")

    _run_cmd(["git", "add", rel])

    commit_msg = (
        f"Add {agent_name} agent manifest\n\n"
        f"{spec.get('description', '')}\n\n"
        f"Target users: {spec.get('target_users', '')}\n"
        f"Tools: {', '.join(spec.get('tools', []))}\n"
        f"Model: {spec['model']['provider']}/{spec['model']['name']}\n\n"
        f"Agent is inactive by default — activate from the dashboard.\n\n"
        f"Generated by scripts/launch_agent.py\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )

    code, _, err = _run_cmd(["git", "commit", "-m", commit_msg])
    if code != 0:
        _error(f"Commit failed: {err}")
        return
    _success("Committed")

    code, _, err = _run_cmd(["git", "push", "-u", "origin", branch])
    if code != 0:
        _error(f"Push failed: {err}")
        return
    _success(f"Pushed: {branch}")

    code, _, _ = _run_cmd(["gh", "--version"])
    if code != 0:
        _warn("gh CLI not installed — open the PR manually.")
        return

    pr_body = (
        f"## New Agent: `{agent_name}`\n\n"
        f"**Description:** {spec.get('description', '')}\n\n"
        f"**Target users:** {spec.get('target_users', '')}\n\n"
        f"**Model:** `{spec['model']['provider']}/{spec['model']['name']}`"
        f" · max `${spec['model']['max_cost_usd']}/run`\n\n"
        f"**Tools:** {', '.join(f'`{t}`' for t in spec.get('tools', []))}\n\n"
        "## Files\n"
        f"- `{rel}`\n\n"
        "## Deploy steps\n"
        "After merge, CI runs:\n"
        "```bash\n"
        f"make ci-deploy AGENT={agent_name}\n"
        "```\n"
        "Then activate from the dashboard:\n"
        "```bash\n"
        f"curl -X PATCH http://localhost:8000/api/v1/agents/{agent_name}/activate \\\n"
        f"  -H 'X-API-Key: <key>'\n"
        "```\n\n"
        "---\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )

    code, stdout, err = _run_cmd([
        "gh", "pr", "create",
        "--title", f"Add {agent_name} agent",
        "--body", pr_body,
    ])

    if code == 0:
        _success(f"PR created: {stdout}")
    else:
        _warn(f"PR creation failed: {err}")
        _info("Branch was pushed — open the PR manually.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fundly Agent Launcher — conversational YAML manifest creator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Note: The launcher creates the YAML manifest only.
      Tool implementations are written by developers and registered separately in
      src/agri_agent/agent/tools/__init__.py before they can be used by agents.

Examples:
  uv run python scripts/launch_agent.py
  uv run python scripts/launch_agent.py --model claude-opus-4-8
  uv run python scripts/launch_agent.py --no-git
        """,
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-6",
        help="Claude model to use (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--no-git", action="store_true",
        help="Skip the git branch / PR step",
    )
    args = parser.parse_args()

    _banner("Fundly Agent Launcher")
    print(f"""
  Creates a new agent YAML manifest through a short conversation.
  The manifest is reviewed, written to agents/configs/, and optionally
  pushed as a GitHub PR for CI/CD deployment.

  After CI/CD deploys the agent it starts as {BOLD}inactive{RESET}.
  A platform admin activates it from the dashboard.

  Model : {BOLD}{args.model}{RESET}
  Press   Ctrl+C to exit at any time.
""")

    # ── Load available tools ──────────────────────────────────────────────────
    tools = _get_available_tools()
    if tools:
        _section("Available tools in registry")
        print(_format_tool_list(tools))
    else:
        _warn("Tool registry unavailable — tool recommendations will be limited.")

    client, model = _make_client(args.model)

    # ── Phase 1: gather requirements ──────────────────────────────────────────
    spec = run_discovery(client, model, tools)
    _success(f"Spec complete — agent: {BOLD}{spec['agent_name']}{RESET}")

    # ── Phase 2: generate YAML ────────────────────────────────────────────────
    yaml_content = run_generation(client, model, spec)

    # ── Phase 3: review and iterate ───────────────────────────────────────────
    yaml_content = run_review(client, model, spec, yaml_content)

    # ── Phase 4: write YAML to disk ───────────────────────────────────────────
    _section("Writing manifest")
    yaml_path = write_yaml(spec, yaml_content)

    # ── Phase 5: git + PR ─────────────────────────────────────────────────────
    git_and_pr(spec, yaml_path, args.no_git)

    # ── Summary ───────────────────────────────────────────────────────────────
    _banner("Done!")
    agent_name = spec["agent_name"]
    print(f"""
  Agent  : {BOLD}{agent_name}{RESET}
  Manifest: {yaml_path.relative_to(ROOT)}
  Status : {BOLD}inactive{RESET} (activate from dashboard after deploy)

  Next steps:
    1. Review and merge the PR (or push manually)
    2. CI/CD deploys:  {BOLD}make ci-deploy AGENT={agent_name}{RESET}
    3. Activate:       {BOLD}PATCH /api/v1/agents/{agent_name}/activate{RESET}
    4. Test:           {BOLD}POST  /api/v1/agents/{agent_name}/run{RESET}

  If new tools are needed, ask a developer to:
    • Implement the tool in  src/agri_agent/agent/tools/
    • Register it in         src/agri_agent/agent/tools/__init__.py
    • Then re-run the Launcher to create a new agent that uses those tools.
""")


if __name__ == "__main__":
    main()
