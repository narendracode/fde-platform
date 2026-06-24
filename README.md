# AgriScience Agent Platform — POC

A centralized AI agent platform built on LangFlow, LangGraph, FastAPI, PostgreSQL, and Redis.

## Quick start

```bash
cp .env.example .env   # fill in your API keys
make up                # starts all services
make migrate           # run DB migrations
make seed              # seed demo agents
```

- LangFlow UI: http://localhost:7860
- Agent API docs: http://localhost:8000/docs

## Run an agent locally (no Docker)

```bash
uv run agri-agent list
uv run agri-agent run react-agent "What crops should I plant in Punjab this rabi season on loamy soil?"
```
