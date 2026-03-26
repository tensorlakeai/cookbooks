# OpenClaw + Tensorlake Sandboxes

Wire [Tensorlake Sandboxes](https://docs.tensorlake.ai/sandboxes/introduction) into OpenClaw's SSH backend so that every agent gets its own isolated, cloud-native execution environment — no Docker, no VMs, no infrastructure to manage.

## How it works

OpenClaw has a pluggable SSH sandbox backend. The `ssh.command` config field replaces the `ssh` binary with any executable. This cookbook provides a shim script that intercepts OpenClaw's SSH calls and translates them into Tensorlake HTTP API calls.

```
OpenClaw process (in Docker)
└── calls tensorlake-openclaw-shim  ← replaces the ssh binary
    ├── GET  api.tensorlake.ai/sandboxes/<id>                — reuse or create
    ├── POST api.tensorlake.ai/sandboxes                     — create new sandbox
    ├── PUT  <sandbox>.sandbox.tensorlake.ai/api/v1/files    — workspace upload
    ├── POST <sandbox>.sandbox.tensorlake.ai/api/v1/processes — run command
    ├── GET  .../processes/<pid>/output/follow               — stream stdout/stderr (SSE)
    ├── GET  .../processes/<pid>                             — poll exit code
    └── DELETE api.tensorlake.ai/sandboxes/<id>             — terminate on session end
```

Each OpenClaw agent gets its own Tensorlake Sandbox: an ephemeral Linux container provisioned in seconds, with real-time output streaming and automatic cleanup.

## Prerequisites

- An OpenClaw instance (self-hosted or Docker)
- A [Tensorlake API key](https://docs.tensorlake.ai/platform/authentication#api-keys)
- `curl` and `python3` (or `jq`) available in the OpenClaw container

## Files

| File | Purpose |
|---|---|
| `tensorlake-openclaw-shim` | The shim script that bridges OpenClaw SSH calls to the Tensorlake HTTP API |
| `openclaw.json` | OpenClaw configuration enabling the SSH sandbox backend |
| `docker-compose.yml` | Docker Compose example — mount the shim without rebuilding |
| `Dockerfile` | Custom image example — bake the shim in |

## Setup

### 1. Get a Tensorlake API key

Sign up at [tensorlake.ai](https://tensorlake.ai) and create an API key from the dashboard.

### 2. Install the shim

The shim must be executable and available in `PATH` inside the OpenClaw container.

**Option A — Mount at runtime (no rebuild required):**

```bash
chmod +x tensorlake-openclaw-shim

docker run \
  -v "$(pwd)/tensorlake-openclaw-shim:/usr/local/bin/tensorlake-openclaw-shim" \
  -e TENSORLAKE_API_KEY="your-api-key" \
  your-openclaw-image
```

Or with Docker Compose — edit `docker-compose.yml` to set your image, then:

```bash
export TENSORLAKE_API_KEY="your-api-key"
docker compose up
```

> The host file must be executable (`chmod +x`) before mounting — Docker bind-mounts preserve host permissions.

**Option B — Bake into a custom image:**

Edit `Dockerfile` to set your base image, then build:

```bash
docker build -t openclaw-tensorlake .

docker run \
  -e TENSORLAKE_API_KEY="your-api-key" \
  openclaw-tensorlake
```

### 3. Configure OpenClaw

The provided `openclaw.json` configures the SSH sandbox backend to use the shim:

```json
{
  "agents": {
    "defaults": {
      "sandbox": {
        "mode": "all",
        "backend": "ssh",
        "scope": "agent",
        "workspaceAccess": "rw",
        "ssh": {
          "target": "ignored@ignored:22",
          "command": "tensorlake-openclaw-shim",
          "workspaceRoot": "/workspace"
        }
      }
    }
  }
}
```

The `target` field is required by OpenClaw but ignored by the shim — all communication goes through the Tensorlake API.

### 4. Verify

Run a smoke test from inside the container:

```bash
docker exec \
  -e TENSORLAKE_API_KEY=your-api-key \
  openclaw_core \
  tensorlake-openclaw-shim 'echo hello from sandbox'
```

Expected output:

```
[tensorlake] creating sandbox (cpus=2.0, memory_mb=4000)…
[tensorlake] sandbox abc123 created, waiting for running…
[tensorlake] sandbox ready: abc123
hello from sandbox
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TENSORLAKE_API_KEY` | — | **Required.** Your Tensorlake API key. |
| `TENSORLAKE_SANDBOX_CPUS` | `2.0` | CPU cores allocated to new sandboxes. |
| `TENSORLAKE_SANDBOX_MEMORY_MB` | `4000` | Memory in MiB allocated to new sandboxes. |
| `TENSORLAKE_SANDBOX_TIMEOUT_SECS` | `3600` | Sandbox auto-terminates after this many seconds. |
| `OPENCLAW_AGENT_ID` | `default` | Scopes the sandbox state file — set automatically by OpenClaw. |

## Manual termination

Stop a sandbox outside of the normal session lifecycle:

```bash
# From the host
TENSORLAKE_API_KEY=your-key \
OPENCLAW_AGENT_ID=my-agent \
tensorlake-openclaw-shim terminate

# From inside the container
docker exec \
  -e TENSORLAKE_API_KEY=your-key \
  -e OPENCLAW_AGENT_ID=my-agent \
  openclaw_core \
  tensorlake-openclaw-shim terminate
```

## How the shim works

A few non-obvious implementation details:

**SSH argument stripping.** OpenClaw calls the shim with SSH-style flags (`-F config -T -o RequestTTY=no`) and the host alias `openclaw-sandbox`. Since that alias has no `@`, the usual `user@host` detection doesn't work. The shim treats the first non-flag argument as the hostname when flags are present, and everything after it as the command.

**Stdin for workspace uploads.** OpenClaw seeds the workspace by piping a tar archive to the shim's stdin. The Tensorlake process API has no stdin channel, so when the command contains `tar -xf -` the shim uploads stdin to a temp file in the sandbox via the file API and rewrites the command to read from it.

**Auto-termination.** OpenClaw sends a `rm -rf` command tagged with the marker `openclaw-sandbox-remove` at the end of a session. The shim detects this and issues a `DELETE /sandboxes/<id>` after the command completes. The `timeout_secs` setting acts as a safety net for sessions that end without a clean teardown.

**State file.** The shim records the sandbox ID in `/tmp/tensorlake-openclaw-<OPENCLAW_AGENT_ID>`. On every invocation it checks whether the recorded sandbox is still running before creating a new one, keeping one sandbox per agent across multiple commands in the same session.

## File I/O

The shim also supports direct file operations for scripting or debugging:

```bash
# Upload a local file to the sandbox
cat myfile.txt | tensorlake-openclaw-shim put /workspace/myfile.txt

# Download a file from the sandbox
tensorlake-openclaw-shim get /workspace/output.txt > output.txt
```
