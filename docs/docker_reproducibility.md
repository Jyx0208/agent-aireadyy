# Docker Reproducibility Guide

This project uses one all-in-one Docker image for the public/reproducible agent environment.

The image contains:

- Web UI and CLI dependencies.
- Discovery and AI-ready exporters.
- Test dependencies.
- Docker CLI, Java, and git for the original full workflow runtime.

The image does not bake in large RAW files, FragPipe result folders, or MSDT output. Full workflow execution still uses Docker socket passthrough to launch the existing MSDT converter image when explicitly requested.

## Build And Start

```powershell
copy .env.example .env
docker compose build web
docker compose up -d web
```

Open:

```text
http://127.0.0.1:8000
```

Run CLI commands inside the same environment:

```powershell
docker compose exec web python -m agent.cli check-runtime
docker compose exec web python -m pytest -q
```

## Full Workflow Safety

Full workflow is disabled from the Web UI by default:

```text
AGENT_WEB_FULL_WORKFLOW_ENABLED=0
```

Set it to `1` only when Docker Desktop or Docker Engine is ready and disk usage is acceptable.

For the local all-in-one Web container, the minimum full-mode settings are:

```text
AGENT_WEB_FULL_WORKFLOW_ENABLED=1
AGENT_CONTAINER_APP_DIR=/app
AGENT_CONTAINER_RUNS_DIR=/app/runs
AGENT_HOST_APP_DIR=<host path to agent-aireadyy>
AGENT_HOST_RUNS_DIR=<host path to agent-aireadyy/runs>
AGENT_MSDT_DOCKER_TIMEOUT_SECONDS=7200
AGENT_MSDT_DOCKER_IDLE_TIMEOUT_SECONDS=900
AGENT_MSDT_ABORT_ON_LOW_PSM=1
```

Windows Docker Desktop example:

```text
AGENT_HOST_APP_DIR=$PWD
AGENT_HOST_RUNS_DIR=$PWD\runs
```

CLI full mode remains explicit:

```powershell
docker compose exec web python -m agent.cli one-click-run "example.raw" --mode full
```

## Nested Docker Path Mapping

The full workflow may launch another Docker container for MSDT conversion.

An optional advanced setup can use:

```text
AGENT_DOCKER_VOLUMES_FROM=<agent-container-name>
```

This asks nested MSDT Docker jobs to inherit the agent container's mounted volumes. Keep it disabled unless you have verified that the inherited mounts do not shadow paths inside the MSDT image. In the current Windows Docker Desktop setup, inheriting the agent container's `/app` mount can hide the converter image's own `/app/convert.py`, so the recommended path is the explicit host-path mapping below.

By default the inner container needs host paths visible to the Docker daemon, so `.env` must set:

```text
AGENT_HOST_APP_DIR=/host/path/to/agent-aireadyy
AGENT_HOST_RUNS_DIR=/host/path/to/agent-aireadyy/runs
```

Linux server example:

```text
AGENT_HOST_APP_DIR=/opt/pride-agent
AGENT_HOST_RUNS_DIR=/opt/pride-agent/runs
```

Docker Desktop / Windows path translation varies by environment. The all-in-one image supports Web, Discovery, AI-ready exporters, tests, and prepare mode on Windows. Fully automated `full` mode from inside the Linux agent container needs a validated host-path helper or Docker-in-Docker setup before it should be advertised as Windows-supported.

Before running full mode with fallback paths, validate Docker access from inside the agent image:

```powershell
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock pride-agent-all-in-one-local docker version
```

If full mode fails with a mount/path error, first check the two fallback host path variables, then consider a Linux host or a dedicated Docker-in-Docker/host-helper setup.

Low-PSM FragPipe/MSBooster stalls are guarded by the watchdog settings above. When the log contains `RT regression using 0 PSMs`, the Web run records `low_psm_msbooster` and stops the nested MSDT container instead of waiting indefinitely. Set `AGENT_MSDT_ABORT_ON_LOW_PSM=0` only when you intentionally want to let that workflow continue.

## What Stays Outside The Image

- Large PRIDE/MassIVE/iProX data.
- `runs/` outputs.
- `.agent_cache/`.
- API keys.
- Optional iProX Excel indexes.

These remain mounted or configured at runtime so the published image stays clean and small.
