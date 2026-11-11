# Edge Device Management and OTA Platform

Monitor a fleet of Linux edge devices and roll out Docker-packaged software
updates to them, with automatic rollback when a new version fails its health check.

> Work in progress. The full README (architecture, sequences, API docs) lands in Stage 10.

## Status

| Stage | Scope | State |
|---|---|---|
| 1 | Device agent: metrics and heartbeat | done |
| 2 | Device registry (FastAPI) | done |
| 3 | Software versions and deployment requests | done |
| 4 | OTA deployment | next |
| 5 | Rollback | |
| 6 | Security | |
| 7 | Monitoring | |
| 8 | Docker Compose | |
| 9 | CI/CD | |
| 10 | Documentation | |

## Device agent quick start

```bash
python -m venv .venv
.venv/bin/pip install -e "agent[dev]"      # Windows: .venv\Scripts\pip
.venv/bin/edge-agent --dry-run --once      # print one heartbeat as JSON
```

Configuration is read from `EDGE_AGENT_*` environment variables; see
[agent/src/edge_agent/config.py](agent/src/edge_agent/config.py).

## Backend quick start

```bash
.venv/bin/pip install -e "backend[dev]"
.venv/bin/uvicorn device_mgmt.main:create_app --factory --reload
```

Interactive API docs are served at http://localhost:8000/docs. Configuration is
read from `DEVICE_MGMT_*` environment variables; see [.env.example](.env.example).
