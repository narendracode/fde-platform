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


def build_flow_json(cfg: AgentConfig,
                    chat_in_id: str | None = None,
                    agent_id: str | None = None,
                    chat_out_id: str | None = None,
                    existing_edges: list | None = None) -> dict:
    """Construct a LangFlow-compatible flow JSON for an AgentConfig.

    Pass stable node IDs when updating an existing flow so that previously
    wired edges (which reference those IDs) remain valid.

    Flow layout:
        ChatInput ──► AgriAgent Custom Component ──► ChatOutput
    """
    chat_in_id = chat_in_id or _node_id("ChatInput")
    agent_id = agent_id or _node_id("CustomComponent")
    chat_out_id = chat_out_id or _node_id("ChatOutput")

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
                    # Pre-declare input_value so LangFlow can validate edges before
                    # it parses the embedded component code (lazy evaluation timing).
                    "input_value": {
                        "type": "str", "required": False, "placeholder": "",
                        "list": False, "show": True, "multiline": True, "value": "",
                        "password": False, "name": "input_value",
                        "display_name": "Message", "advanced": False,
                        "info": "User message sent to the agent.",
                        "load_from_db": False, "title_case": False,
                        "input_types": ["Message"],
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
                "field_order": ["code", "input_value"],
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
                        "input_types": ["Message"],
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
    # LangFlow 1.x ReactFlow edge format:
    #   - id must be  reactflow__edge-{source}{outputName}-{target}{inputName}
    #   - sourceHandle / targetHandle are JSON-encoded strings
    #   - data must contain the same dicts parsed (not stringified)
    #   - selected: false required for proper import
    src_h1 = {"dataType": "ChatInput", "id": chat_in_id, "name": "message", "output_types": ["Message"]}
    tgt_h1 = {"fieldName": "input_value", "id": agent_id, "inputTypes": ["Message"], "type": "str"}
    src_h2 = {"dataType": "CustomComponent", "id": agent_id, "name": "output_value", "output_types": ["Message"]}
    tgt_h2 = {"fieldName": "input_value", "id": chat_out_id, "inputTypes": ["Message"], "type": "str"}

    edges = [
        {
            "id": f"reactflow__edge-{chat_in_id}message-{agent_id}input_value",
            "source": chat_in_id, "target": agent_id,
            "sourceHandle": json.dumps(src_h1),
            "targetHandle": json.dumps(tgt_h1),
            "data": {"sourceHandle": src_h1, "targetHandle": tgt_h1},
            "animated": False, "selected": False, "type": "default",
        },
        {
            "id": f"reactflow__edge-{agent_id}output_value-{chat_out_id}input_value",
            "source": agent_id, "target": chat_out_id,
            "sourceHandle": json.dumps(src_h2),
            "targetHandle": json.dumps(tgt_h2),
            "data": {"sourceHandle": src_h2, "targetHandle": tgt_h2},
            "animated": False, "selected": False, "type": "default",
        },
    ]

    # Prefer caller-supplied edges (stable after manual wiring) over freshly built ones
    final_edges = existing_edges if existing_edges else edges

    return {
        "name": cfg.name,
        "description": cfg.description.strip(),
        "endpoint_name": cfg.name,  # enables /api/v1/run/{cfg.name}
        "tags": [PLATFORM_TAG],
        "data": {
            "nodes": [chat_input_node, agent_node, chat_output_node],
            "edges": final_edges,
            "viewport": {"x": 0, "y": 0, "zoom": 0.85},
        },
    }


# ── LangFlow API helpers ──────────────────────────────────────────────────────

def get_flow(client: httpx.Client, token: str, flow_id: str) -> dict:
    resp = client.get(
        f"{LANGFLOW_URL}/api/v1/flows/{flow_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _translate_edges(edges: list, id_map: dict[str, str]) -> list:
    """Rewrite node IDs inside edges so they point to new node IDs after a recreate."""
    import copy
    result = []
    for edge in edges:
        e = copy.deepcopy(edge)
        e["source"] = id_map.get(e.get("source", ""), e.get("source", ""))
        e["target"] = id_map.get(e.get("target", ""), e.get("target", ""))
        for hkey in ("sourceHandle", "targetHandle"):
            if isinstance(e.get(hkey), str):
                try:
                    h = json.loads(e[hkey])
                    h["id"] = id_map.get(h.get("id", ""), h.get("id", ""))
                    e[hkey] = json.dumps(h)
                except (json.JSONDecodeError, KeyError):
                    pass
            if isinstance(e.get("data", {}).get(hkey), dict):
                h = e["data"][hkey]
                h["id"] = id_map.get(h.get("id", ""), h.get("id", ""))
        # Keep edge ID consistent with the reactflow__ convention
        for old_id, new_id in id_map.items():
            e["id"] = e.get("id", "").replace(old_id, new_id)
        result.append(e)
    return result


def _extract_node_ids(flow: dict) -> tuple[str | None, str | None, str | None]:
    """Return (chat_in_id, agent_id, chat_out_id) from an existing flow.

    Identifies nodes by their stored type field so IDs stay stable across syncs.
    """
    nodes = flow.get("data", {}).get("nodes", [])
    ids: dict[str, str] = {}
    for n in nodes:
        ntype = n.get("data", {}).get("type", "")
        if ntype == "ChatInput":
            ids["chat_in"] = n["id"]
        elif ntype == "ChatOutput":
            ids["chat_out"] = n["id"]
        elif ntype == "CustomComponent":
            ids["agent"] = n["id"]
    return ids.get("chat_in"), ids.get("agent"), ids.get("chat_out")


def list_all_flows(client: httpx.Client, token: str) -> list[dict]:
    resp = client.get(
        f"{LANGFLOW_URL}/api/v1/flows/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def list_platform_flows(client: httpx.Client, token: str,
                        also_match_names: set[str] | None = None) -> dict[str, str]:
    """Return {name: flow_id} for flows we own (tagged) or that match known agent names.

    also_match_names catches flows created by old script versions that didn't
    apply the PLATFORM_TAG yet, so --delete-all and updates don't miss them.
    """
    flows = list_all_flows(client, token)
    result = {}
    for f in flows:
        has_tag = PLATFORM_TAG in (f.get("tags") or [])
        name_match = also_match_names and f["name"] in also_match_names
        if has_tag or name_match:
            result[f["name"]] = f["id"]
    return result


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
               client: httpx.Client, token: str,
               dry_run: bool, force_recreate: bool = False) -> dict:
    flow_json = build_flow_json(cfg)
    saved_edges: list | None = None

    if dry_run:
        print(json.dumps(flow_json, indent=2))
        return {"name": cfg.name, "action": "dry-run"}

    try:
        if cfg.name in existing and not force_recreate:
            flow_id = existing[cfg.name]
            # Fetch the stored flow to reuse its node IDs and preserve any
            # edges the user wired manually — new random IDs would invalidate them.
            stored = get_flow(client, token, flow_id)
            chat_in_id, agent_id, chat_out_id = _extract_node_ids(stored)
            stored_edges = stored.get("data", {}).get("edges") or []
            flow_json = build_flow_json(
                cfg,
                chat_in_id=chat_in_id,
                agent_id=agent_id,
                chat_out_id=chat_out_id,
                existing_edges=stored_edges if stored_edges else None,
            )
            result = update_flow(client, token, flow_id, flow_json)
            action = "updated"
        else:
            saved_edges = None
            if cfg.name in existing and force_recreate:
                # Preserve any manually-wired edges by translating their node IDs
                # to the new ones we're about to generate.
                stored = get_flow(client, token, existing[cfg.name])
                old_chat_in, old_agent_id, old_chat_out = _extract_node_ids(stored)
                old_edges = stored.get("data", {}).get("edges") or []
                if old_edges and all([old_chat_in, old_agent_id, old_chat_out]):
                    new_chat_in = _node_id("ChatInput")
                    new_agent   = _node_id("CustomComponent")
                    new_chat_out = _node_id("ChatOutput")
                    id_map = {old_chat_in: new_chat_in,
                              old_agent_id: new_agent,
                              old_chat_out: new_chat_out}
                    saved_edges = _translate_edges(old_edges, id_map)
                    flow_json = build_flow_json(cfg, new_chat_in, new_agent, new_chat_out,
                                               existing_edges=saved_edges)
                delete_flow(client, token, existing[cfg.name])
            result = create_flow(client, token, flow_json)
            flow_id = result["id"]
            action = "recreated" if force_recreate else "created"

        return {
            "name": cfg.name,
            "action": action,
            "flow_id": flow_id,
            "had_edges": bool(saved_edges) if action in ("created", "recreated") else True,
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
    parser.add_argument("--force-recreate", action="store_true",
                        help="Delete and re-create flows instead of updating in place "
                             "(fixes broken edge state; wired connections will need re-wiring once)")
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
            # Resolve configs first so we can match by name (catches untagged legacy flows)
            all_configs = list_agent_configs()
            known_names = {c.name for c in all_configs}
            existing = list_platform_flows(client, token, also_match_names=known_names)
            print(f"Found {len(existing)} existing platform flow(s) in LangFlow\n")
        else:
            token = ""
            existing = {}
            all_configs = list_agent_configs()

        if args.delete_all:
            for name, fid in existing.items():
                delete_flow(client, token, fid)
                print(f"  ✗  deleted: {name}")
            print(f"\nDeleted {len(existing)} flow(s).")
            return

        if args.agent:
            configs = [load_agent_config(args.agent)]
        else:
            configs = all_configs

        if not configs:
            print("No agent configs found in agents/configs/")
            return

        print(f"Syncing {len(configs)} config(s)...\n")
        results = []
        for cfg in configs:
            r = sync_agent(cfg, existing, client, token,
                           dry_run=args.dry_run, force_recreate=args.force_recreate)
            print_result(r)
            results.append(r)

        if not args.dry_run:
            print_api_examples(results)
            errors = [r for r in results if r.get("action") == "error"]
            if errors:
                print(f"\n[ERROR] {len(errors)} flow(s) failed to sync.")
            created = [r for r in results if r.get("action") in ("created", "recreated")
                       and not r.get("had_edges")]
            if created:
                names = ", ".join(r["name"] for r in created)
                print(f"\n[ONE-TIME WIRING NEEDED] {names}")
                print("  LangFlow Custom Components register their inputs/outputs lazily")
                print("  (after the component code is parsed), so edges cannot be pre-wired")
                print("  via the API. Wire the nodes once in the UI:")
                print("    1. Open the flow → click the AgriAgent component to build it")
                print("    2. Drag: Chat Input → AgriAgent → Chat Output")
                print("    3. Click Save")
                print("  All future 'make sync-flows' calls will preserve these connections.")


if __name__ == "__main__":
    main()
