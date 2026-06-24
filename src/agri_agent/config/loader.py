"""Load and validate YAML agent config files into typed Pydantic models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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


class AgentConfig(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    model: ModelConfig = ModelConfig()
    system_prompt: str = "You are a helpful AI assistant."
    tools: list[ToolConfig] = []
    guardrails: GuardrailsConfig = GuardrailsConfig()
    observability: ObservabilityConfig = ObservabilityConfig()

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
