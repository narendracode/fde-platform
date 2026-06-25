# Fundly Agent Platform

A centralized AI agent platform built on LangGraph, FastAPI, PostgreSQL, and Redis.

## Quick start

```bash
cp .env.example .env   # fill in your API keys
make up                # starts all services (postgres, redis, api, worker, jaeger, adminer)
make migrate           # run DB migrations
make seed              # seed demo agents
```

- Agent API docs: http://localhost:8000/docs
- Jaeger UI: http://localhost:16686
- Adminer (DB browser): http://localhost:8080

## Run an agent locally (no Docker)

```bash
uv run agri-agent list
uv run agri-agent run pharma-outreach "Run outreach for Mumbai"
```
