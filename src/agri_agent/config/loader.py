"""Load and validate YAML agent config files into typed Pydantic models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, field_validator

from agri_agent.config.settings import settings


# ── Sub-models ────────────────────────────────────────────────────────────────

class ModelConfig(BaseModel):
    provider: str = "anthropic"
    name: str = "claude-sonnet-4-6"
    temperature: float = 0.2
    max_tokens: int = 4096
    max_cost_usd: float = 1.0


class ToolConfig(BaseModel):
    name: str
    enabled: bool = True
    config: dict[str, Any] = {}


class GuardrailsConfig(BaseModel):
    max_iterations: int = 15
    timeout_seconds: int = 120
    blocked_patterns: list[str] = []


class ObservabilityConfig(BaseModel):
    langsmith_tracing: bool = True
    log_inputs: bool = True
    log_outputs: bool = True
    log_tool_calls: bool = True


class InputParam(BaseModel):
    """Declaration of a single runtime input parameter for an agent.

    Declared under the `inputs:` key in the YAML.  At run time the caller
    passes values via the `extra_context` field of the RunRequest.  The
    platform validates required params, applies defaults, and injects all
    resolved values into the agent's message as a structured context block.
    """
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = True
    default: Any = None
    description: str = ""

    def cast(self, value: Any) -> Any:
        """Coerce a string value (e.g. from JSON) to the declared type."""
        if self.type == "integer":
            return int(value)
        if self.type == "number":
            return float(value)
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "yes")
        return str(value)


class WorkerRef(BaseModel):
    """Reference to a worker agent used by a supervisor."""
    agent: str          # agent name — must match a YAML config filename/name
    description: str = ""  # shown to the supervisor LLM to help it choose the right worker


class RoutingConfig(BaseModel):
    """Supervisor routing behaviour."""
    max_rounds: int = 5   # max supervisor↔worker cycles before hard stop


class AgentConfig(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    # "react" = standard ReAct agent (default)
    # "supervisor" = orchestrates a set of worker agents
    type: Literal["react", "supervisor"] = "react"
    model: ModelConfig = ModelConfig()
    system_prompt: str = "You are a helpful AI assistant."
    inputs: dict[str, InputParam] = {}
    tools: list[ToolConfig] = []
    guardrails: GuardrailsConfig = GuardrailsConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    feature_flags: dict[str, Any] = {}
    # Supervisor-only fields (ignored for react agents)
    workers: list[WorkerRef] = []
    routing: RoutingConfig = RoutingConfig()
    # Visibility scoping — empty list means platform agent (always shown).
    # Populate with company slugs matching COMPANIES_TO_SHOW values.
    companies: list[str] = []

    @field_validator("name")
    @classmethod
    def name_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("Agent name must be lowercase alphanumeric with hyphens")
        return v

    def enabled_tools(self) -> list[str]:
        return [t.name for t in self.tools if t.enabled]

    def tool_config(self, tool_name: str) -> dict[str, Any]:
        for t in self.tools:
            if t.name == tool_name:
                return t.config
        return {}

    def resolve_context(self, provided: dict[str, Any]) -> dict[str, Any]:
        """Validate and resolve runtime inputs against the declared schema.

        - Required params with no value raise ValueError.
        - Optional params with no value get their declared default.
        - Values are type-cast to the declared type.
        - Extra keys not in the schema pass through unchanged.

        Returns the fully resolved context dict ready for injection.
        """
        resolved: dict[str, Any] = {}

        for name, param in self.inputs.items():
            if name in provided:
                resolved[name] = param.cast(provided[name])
            elif not param.required and param.default is not None:
                resolved[name] = param.cast(param.default)
            elif param.required:
                raise ValueError(
                    f"Required input '{name}' ({param.description or param.type}) "
                    f"was not provided. Pass it via extra_context."
                )

        # Pass through any caller-supplied keys not declared in the schema
        for k, v in provided.items():
            if k not in resolved:
                resolved[k] = v

        return resolved


# ── Loader ────────────────────────────────────────────────────────────────────

def load_agent_config(name: str) -> AgentConfig:
    """Load an agent config by name from the configs directory.

    Tries <name>.yaml first, then scans all YAML files for one whose
    agent.name field matches (handles slug mismatches like hyphens vs underscores).
    """
    config_dir = Path(settings.agents_config_dir)
    # Direct filename match first
    for candidate in (f"{name}.yaml", f"{name.replace('-', '_')}.yaml"):
        path = config_dir / candidate
        if path.exists():
            raw = yaml.safe_load(path.read_text())
            agent_raw = raw.get("agent", raw)
            return AgentConfig.model_validate(agent_raw)
    # Fallback: scan by agent.name field
    for path in config_dir.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        agent_raw = raw.get("agent", raw)
        if agent_raw.get("name") == name:
            return AgentConfig.model_validate(agent_raw)
    raise FileNotFoundError(f"Agent config '{name}' not found in {config_dir}")


def agent_is_visible(cfg: AgentConfig, active_companies: list[str]) -> bool:
    """Return True if an agent should be shown given the active company list.

    An empty `companies` list means the agent is platform-wide (always visible).
    Otherwise the agent is visible only if at least one of its declared companies
    is in `active_companies`.
    """
    if not cfg.companies:
        return True
    return bool(set(cfg.companies) & set(active_companies))


def list_agent_configs() -> list[AgentConfig]:
    """Return all valid agent configs found in the configs directory."""
    config_dir = Path(settings.agents_config_dir)
    configs = []
    for path in sorted(config_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text())
            agent_raw = raw.get("agent", raw)
            configs.append(AgentConfig.model_validate(agent_raw))
        except Exception:
            pass
    return configs
