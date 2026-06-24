"""Sync YAML agent configs → LangFlow flows.

Simulates what a CI/CD pipeline would do after a YAML file is merged.
Usage:
    uv run python scripts/sync_langflow_flows.py                  # sync all
    uv run python scripts/sync_langflow_flows.py --agent react-agent   # sync one
    uv run python scripts/sync_langflow_flows.py --dry-run        # print JSON, don't send
    uv run python scripts/sync_langflow_flows.py --delete-all     # remove all platform flows
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from textwrap import dedent

import httpx

# ── Allow running from project root ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agri_agent.config.loader import AgentConfig, list_agent_configs, load_agent_config
from agri_agent.config.settings import settings

LANGFLOW_URL = os.getenv("LANGFLOW_URL", "http://localhost:7860")
LANGFLOW_USER = os.getenv("LANGFLOW_SUPERUSER", "admin")
LANGFLOW_PASS = os.getenv("LANGFLOW_SUPERUSER_PASSWORD", "adminpass123")

# Tag added to every flow we create so we can identify and manage them
PLATFORM_TAG = "agri-platform"


# ── Authentication ─────────────────────────────────────────────────────────────

def get_token(client: httpx.Client) -> str:
    resp = client.post(
        f"{LANGFLOW_URL}/api/v1/login",
        data={"username": LANGFLOW_USER, "password": LANGFLOW_PASS},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"  [ERROR] LangFlow login failed ({resp.status_code}): {resp.text}")
        print(f"  Is LangFlow running at {LANGFLOW_URL}?")
        sys.exit(1)
    return resp.json()["access_token"]


# ── Flow JSON builder ─────────────────────────────────────────────────────────

def _node_id(component_type: str) -> str:
    return f"{component_type}-{uuid.uuid4().hex[:8]}"


def _custom_component_code(cfg: AgentConfig) -> str:
    """Python source embedded into LangFlow as a Custom Component.
    Calls our FastAPI platform endpoint so all execution stays in the platform.
    """
    class_name = cfg.name.replace("-", "_").title().replace("_", "")
    tools_list = ", ".join(cfg.enabled_tools())
    return dedent(f'''\
        from langflow.custom import Component
        from langflow.io import MessageTextInput, Output
        from langflow.schema.message import Message
        import httpx

        class {class_name}(Component):
            display_name = "{cfg.name}"
            description = "{cfg.description.strip()}"
            icon = "\\U0001f33e"  # 🌾

            inputs = [
                MessageTextInput(
                    name="input_value",
                    display_name="Message",
                    info="User message sent to the agent.",
                )
            ]

            outputs = [
                Output(
                    display_name="Agent Response",
                    name="output_value",
                    method="run_agent",
                )
            ]

            def run_agent(self) -> Message:
                """Call the AgriScience platform API and return the agent response."""
                try:
                    resp = httpx.post(
                        "{LANGFLOW_URL.replace('localhost:7860', 'api:8000') if 'localhost' in LANGFLOW_URL else LANGFLOW_URL.replace('7860', '8000')}/api/v1/agents/{cfg.name}/run",
                        json={{"message": self.input_value}},
                        headers={{"X-API-Key": "{settings.api_key}"}},
                        timeout=180,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    tool_calls = data.get("tool_calls", [])
                    suffix = ""
                    if tool_calls:
                        names = ", ".join(tc.get("name", "") for tc in tool_calls)
                        suffix = f"\\n\\n_Tools used: {{names}}_"
                    return Message(text=data.get("output", "No response") + suffix)
                except httpx.HTTPStatusError as e:
                    return Message(text=f"API error {{e.response.status_code}}: {{e.response.text}}")
                except Exception as e:
                    return Message(text=f"Error: {{e}}")

        # Agent info (read-only display)
        # Model  : {cfg.model.provider}/{cfg.model.name}
        # Tools  : {tools_list}
        # Version: {cfg.version}
    ''')


def build_flow_json(cfg: AgentConfig) -> dict:
    """Construct a LangFlow-compatible flow JSON for an AgentConfig.

    Flow layout:
        ChatInput ──► AgriAgent Custom Component ──► ChatOutput
    """
    chat_in_id = _node_id("ChatInput")
    agent_id = _node_id("CustomComponent")
    chat_out_id = _node_id("ChatOutput")

    code = _custom_component_code(cfg)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    chat_input_node = {
        "id": chat_in_id,
        "type": "genericNode",
        "position": {"x": 100, "y": 350},
        "data": {
            "showNode": True,
            "type": "ChatInput",
            "id": chat_in_id,
            "node": {
                "template": {
                    "_type": "Component",
                    "input_value": {
                        "type": "str", "required": False, "placeholder": "Type a message...",
                        "list": False, "show": True, "multiline": True, "value": "",
                        "password": False, "name": "input_value", "display_name": "Text",
                        "advanced": False, "info": "Message for the agent.",
                        "load_from_db": False,
                    },
                    "should_store_message": {
                        "type": "bool", "required": False, "list": False, "show": True,
                        "value": True, "name": "should_store_message",
                        "display_name": "Store Messages", "advanced": False,
                    },
                    "sender": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "User", "name": "sender", "display_name": "Sender Type",
                        "advanced": True, "options": ["Machine", "User"],
                    },
                    "sender_name": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "User", "name": "sender_name", "display_name": "Sender Name",
                        "advanced": True,
                    },
                    "session_id": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "", "name": "session_id", "display_name": "Session ID",
                        "advanced": True,
                    },
                },
                "description": "Get chat inputs from the Playground.",
                "base_classes": ["Message"],
                "display_name": "Chat Input",
                "documentation": "",
                "outputs": [
                    {"name": "message", "display_name": "Message", "types": ["Message"]}
                ],
            },
        },
    }

    agent_node = {
        "id": agent_id,
        "type": "genericNode",
        "position": {"x": 550, "y": 350},
        "data": {
            "showNode": True,
            "type": "CustomComponent",
            "id": agent_id,
            "node": {
                "template": {
                    "_type": "CustomComponent",
                    "code": {
                        "type": "code", "required": True, "placeholder": "",
                        "list": False, "show": False, "multiline": True,
                        "value": code, "password": False, "name": "code",
                        "advanced": False, "dynamic": True, "info": "",
                        "load_from_db": False, "title_case": False,
                        "display_name": "Code",
                    },
                },
                "description": f"AgriScience agent: {cfg.name}",
                "base_classes": ["Message"],
                "display_name": cfg.name,
                "documentation": "",
                "custom_fields": {"input_value": "Message"},
                "output_types": ["Message"],
                "outputs": [
                    {"name": "output_value", "display_name": "Agent Response", "types": ["Message"]}
                ],
                "field_order": ["code"],
                "beta": False,
                "edited": False,
            },
        },
    }

    chat_output_node = {
        "id": chat_out_id,
        "type": "genericNode",
        "position": {"x": 1000, "y": 350},
        "data": {
            "showNode": True,
            "type": "ChatOutput",
            "id": chat_out_id,
            "node": {
                "template": {
                    "_type": "Component",
                    "input_value": {
                        "type": "str", "required": False, "placeholder": "",
                        "list": False, "show": True, "multiline": True, "value": "",
                        "password": False, "name": "input_value", "display_name": "Text",
                        "advanced": False, "info": "", "load_from_db": False,
                    },
                    "should_store_message": {
                        "type": "bool", "required": False, "list": False, "show": True,
                        "value": True, "name": "should_store_message",
                        "display_name": "Store Messages", "advanced": False,
                    },
                    "sender": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "Machine", "name": "sender", "display_name": "Sender Type",
                        "advanced": True, "options": ["Machine", "User"],
                    },
                    "sender_name": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "AI", "name": "sender_name", "display_name": "Sender Name",
                        "advanced": True,
                    },
                    "session_id": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "", "name": "session_id", "display_name": "Session ID",
                        "advanced": True,
                    },
                    "data_template": {
                        "type": "str", "required": False, "list": False, "show": True,
                        "value": "{text}", "name": "data_template",
                        "display_name": "Data Template", "advanced": True,
                    },
                },
                "description": "Display a chat message in the Playground.",
                "base_classes": ["Message"],
                "display_name": "Chat Output",
                "documentation": "",
                "outputs": [
                    {"name": "message", "display_name": "Message", "types": ["Message"]}
                ],
            },
        },
    }

    # ── Edges ─────────────────────────────────────────────────────────────────
    # Handle format: JSON-encoded strings (LangFlow 1.x ReactFlow convention)
    edge_1_src_handle = json.dumps({
        "dataType": "ChatInput", "id": chat_in_id,
        "name": "message", "output_types": ["Message"],
    })
    edge_1_tgt_handle = json.dumps({
        "fieldName": "input_value", "id": agent_id,
        "inputTypes": ["Message"], "type": "str",
    })
    edge_2_src_handle = json.dumps({
        "dataType": "CustomComponent", "id": agent_id,
        "name": "output_value", "output_types": ["Message"],
    })
    edge_2_tgt_handle = json.dumps({
        "fieldName": "input_value", "id": chat_out_id,
        "inputTypes": ["Message"], "type": "str",
    })

    edges = [
        {
            "source": chat_in_id, "target": agent_id,
            "id": f"edge-{uuid.uuid4().hex[:8]}",
            "sourceHandle": edge_1_src_handle,
            "targetHandle": edge_1_tgt_handle,
            "animated": False, "data": {}, "type": "default",
        },
        {
            "source": agent_id, "target": chat_out_id,
            "id": f"edge-{uuid.uuid4().hex[:8]}",
            "sourceHandle": edge_2_src_handle,
            "targetHandle": edge_2_tgt_handle,
            "animated": False, "data": {}, "type": "default",
        },
    ]

    return {
        "name": cfg.name,
        "description": cfg.description.strip(),
        "endpoint_name": cfg.name,  # enables /api/v1/run/{cfg.name}
        "tags": [PLATFORM_TAG],
        "data": {
            "nodes": [chat_input_node, agent_node, chat_output_node],
            "edges": edges,
            "viewport": {"x": 0, "y": 0, "zoom": 0.85},
        },
    }


# ── LangFlow API helpers ──────────────────────────────────────────────────────

def list_platform_flows(client: httpx.Client, token: str) -> dict[str, str]:
    """Return {name: flow_id} for all flows we previously created."""
    resp = client.get(
        f"{LANGFLOW_URL}/api/v1/flows/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return {
        f["name"]: f["id"]
        for f in resp.json()
        if PLATFORM_TAG in (f.get("tags") or [])
    }


def create_flow(client: httpx.Client, token: str, flow: dict) -> dict:
    resp = client.post(
        f"{LANGFLOW_URL}/api/v1/flows/",
        json=flow,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def update_flow(client: httpx.Client, token: str, flow_id: str, flow: dict) -> dict:
    resp = client.put(
        f"{LANGFLOW_URL}/api/v1/flows/{flow_id}",
        json=flow,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def delete_flow(client: httpx.Client, token: str, flow_id: str) -> None:
    client.delete(
        f"{LANGFLOW_URL}/api/v1/flows/{flow_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    ).raise_for_status()


# ── Sync logic ────────────────────────────────────────────────────────────────

def sync_agent(cfg: AgentConfig, existing: dict[str, str],
               client: httpx.Client, token: str, dry_run: bool) -> dict:
    flow_json = build_flow_json(cfg)

    if dry_run:
        print(json.dumps(flow_json, indent=2))
        return {"name": cfg.name, "action": "dry-run"}

    try:
        if cfg.name in existing:
            flow_id = existing[cfg.name]
            result = update_flow(client, token, flow_id, flow_json)
            action = "updated"
        else:
            result = create_flow(client, token, flow_json)
            flow_id = result["id"]
            action = "created"

        return {
            "name": cfg.name,
            "action": action,
            "flow_id": flow_id,
            "playground_url": f"{LANGFLOW_URL}/flow/{flow_id}",
            "run_endpoint": f"{LANGFLOW_URL}/api/v1/run/{cfg.name}",
        }
    except httpx.HTTPStatusError as e:
        return {
            "name": cfg.name,
            "action": "error",
            "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
        }


def print_result(r: dict) -> None:
    action = r.get("action", "?")
    name = r["name"]
    if action == "error":
        print(f"  ✗  {name}  ERROR: {r['error']}")
    elif action == "dry-run":
        print(f"  ~  {name}  (dry-run — no changes made)")
    else:
        symbol = "✓" if action in ("created", "updated") else "~"
        print(f"  {symbol}  {name}  [{action}]")
        if "flow_id" in r:
            print(f"       Playground : {r['playground_url']}")
            print(f"       API run    : POST {r['run_endpoint']}")


def print_api_examples(results: list[dict]) -> None:
    ok = [r for r in results if r.get("flow_id")]
    if not ok:
        return
    r = ok[0]
    print("\n" + "─" * 60)
    print("Example API calls (see docs/launch-new-agent.md for full reference)")
    print("─" * 60)
    print(f"\n# Run via LangFlow endpoint (replace message)")
    print(f"curl -s -X POST '{r['run_endpoint']}' \\")
    print(f"  -H 'Content-Type: application/json' \\")
    print(f"  -d '{{\"input_value\": \"What crops for Punjab rabi season?\", \"output_type\": \"chat\", \"input_type\": \"chat\"}}'")
    print(f"\n# Run via platform API directly")
    print(f"curl -s -X POST 'http://localhost:8000/api/v1/agents/{r['name']}/run' \\")
    print(f"  -H 'Content-Type: application/json' \\")
    print(f"  -H 'X-API-Key: {settings.api_key}' \\")
    print(f"  -d '{{\"message\": \"What crops for Punjab rabi season?\"}}'")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global LANGFLOW_URL
    parser = argparse.ArgumentParser(description="Sync YAML agent configs to LangFlow")
    parser.add_argument("--agent", help="Sync only this agent (name without .yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Print flow JSON, don't send")
    parser.add_argument("--delete-all", action="store_true",
                        help="Delete all platform-managed flows from LangFlow")
    parser.add_argument("--langflow-url", default=LANGFLOW_URL,
                        help=f"LangFlow base URL (default: {LANGFLOW_URL})")
    args = parser.parse_args()

    LANGFLOW_URL = args.langflow_url

    print(f"LangFlow sync  →  {LANGFLOW_URL}")

    with httpx.Client() as client:
        if not args.dry_run:
            token = get_token(client)
            existing = list_platform_flows(client, token)
            print(f"Found {len(existing)} existing platform flow(s) in LangFlow\n")
        else:
            token = ""
            existing = {}

        if args.delete_all:
            for name, fid in existing.items():
                delete_flow(client, token, fid)
                print(f"  ✗  deleted: {name}")
            print(f"\nDeleted {len(existing)} flow(s).")
            return

        if args.agent:
            configs = [load_agent_config(args.agent)]
        else:
            configs = list_agent_configs()

        if not configs:
            print("No agent configs found in agents/configs/")
            return

        print(f"Syncing {len(configs)} config(s)...\n")
        results = []
        for cfg in configs:
            r = sync_agent(cfg, existing, client, token, dry_run=args.dry_run)
            print_result(r)
            results.append(r)

        if not args.dry_run:
            print_api_examples(results)
            errors = [r for r in results if r.get("action") == "error"]
            if errors:
                print(f"\n[WARN] {len(errors)} flow(s) failed. "
                      "If edges are missing, open the flow in LangFlow UI and wire nodes manually. "
                      "This is a one-time step — future syncs will update that flow in place.")


if __name__ == "__main__":
    main()
