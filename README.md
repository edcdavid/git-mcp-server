# Git MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that exposes Git operations over Streamable HTTP transport. It wraps the official `mcp-server-git` tools and extends them with repository cloning, file management, search, and push capabilities — enabling AI agents to interact with any Git repository remotely.

## Features

- **Clone & manage repositories** — clone from any Git remote (including Gitea), pull updates, and list available repos
- **Full Git workflow** — status, diff, add, commit, push, branch, checkout, log, show, and reset
- **File operations** — list, read, write, edit, and search files within cloned repositories
- **HTTP authentication** — optional username/password credentials embedded securely in remote URLs
- **Stateless HTTP transport** — Streamable HTTP via `/mcp`, suitable for multi-tenant or load-balanced deployments
- **Health endpoint** — `/healthz` for readiness and liveness probes

## Architecture

```
┌────────────┐   Streamable HTTP    ┌──────────────────┐      ┌──────────┐
│  MCP Client│ ──────────────────── │  git-mcp-server  │ ──── │ /repos   │
│  (AI Agent)│     POST /mcp        │  (Starlette +    │      │ (cloned  │
└────────────┘                      │   uvicorn)       │      │  repos)  │
                                    └──────────────────┘      └──────────┘
```

The server runs as a single Python process that:

1. Accepts MCP tool calls over Streamable HTTP on `/mcp`
2. Clones repositories to a local `REPOS_DIR` (default `/repos`)
3. Delegates standard Git operations to `mcp-server-git` and handles custom tools (clone, file I/O, search, push) directly

## Quick Start

### Run locally

```bash
pip install mcp-server-git "mcp[cli]>=1.8.0" uvicorn starlette gitpython
python server.py
```

The server starts on port **8080** by default.

### Run with Docker

```bash
docker build -t git-mcp-server .
docker run -p 8080:8080 git-mcp-server
```

To persist cloned repos across restarts, mount a volume:

```bash
docker run -p 8080:8080 -v /my/repos:/repos git-mcp-server
```

### Deploy on Kubernetes / OpenShift

Pre-built manifests are in `k8s/deployment.yaml` targeting the `openshift-lightspeed` namespace. Adjust the image reference and namespace as needed, then apply:

```bash
kubectl apply -f k8s/deployment.yaml
```

This creates a Deployment (with health probes) and a ClusterIP Service on port 8080.

## Configuration

| Environment variable | Default  | Description                            |
|----------------------|----------|----------------------------------------|
| `REPOS_DIR`          | `/repos` | Directory where repositories are cloned |

## MCP Endpoint

| Path       | Method | Description              |
|------------|--------|--------------------------|
| `/mcp`     | POST   | MCP Streamable HTTP endpoint |
| `/healthz` | GET    | Health check (returns `{"status": "ok"}`) |

## Available Tools

### Repository management

| Tool             | Description |
|------------------|-------------|
| `git_clone`      | Clone a remote repository (or pull if already cloned). Supports optional branch, username, and password. |
| `git_list_repos` | List all repositories currently cloned in the workspace. |
| `git_push`       | Push committed changes to a remote. |

### File operations

| Tool              | Description |
|-------------------|-------------|
| `git_list_files`  | List files/directories in a repo. Supports subdirectory, recursive, and glob pattern filtering. |
| `git_read_file`   | Read file contents (optionally at a specific revision). |
| `git_write_file`  | Write or overwrite a file, creating parent directories as needed. |
| `git_edit_file`   | Find-and-replace a unique text segment in a file. |
| `git_search_files`| Grep for a pattern across repo files with optional case-insensitive search. |

### Standard Git tools (from `mcp-server-git`)

| Tool                | Description |
|---------------------|-------------|
| `git_status`        | Show working tree status |
| `git_diff_unstaged` | Show unstaged changes |
| `git_diff_staged`   | Show staged changes |
| `git_diff`          | Diff between branches or commits |
| `git_add`           | Stage files |
| `git_commit`        | Commit staged changes |
| `git_reset`         | Unstage all staged changes |
| `git_log`           | Show commit history |
| `git_create_branch` | Create a new branch |
| `git_checkout`      | Switch branches |
| `git_show`          | Show commit contents |
| `git_branch`        | List branches |

## Client Configuration

### Cursor / AI Agent (Streamable HTTP)

Configure your MCP client to connect via Streamable HTTP:

```json
{
  "mcpServers": {
    "git-mcp-server": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

## Example Workflow

A typical AI agent session:

1. **Clone** a repository:
   ```
   git_clone(url="https://github.com/org/repo.git")
   → "Cloned to /repos/repo. Use repo_path='/repos/repo' for other git tools."
   ```

2. **Explore** the repository:
   ```
   git_list_files(repo_path="/repos/repo", recursive=true)
   ```

3. **Read** a file:
   ```
   git_read_file(repo_path="/repos/repo", file_path="src/main.py")
   ```

4. **Edit** a file:
   ```
   git_edit_file(repo_path="/repos/repo", file_path="src/main.py",
                 old_text="old code", new_text="new code")
   ```

5. **Commit and push**:
   ```
   git_add(repo_path="/repos/repo", files=["src/main.py"])
   git_commit(repo_path="/repos/repo", message="Fix bug in main")
   git_push(repo_path="/repos/repo")
   ```

## Project Structure

```
git-mcp-server/
├── server.py              # MCP server implementation
├── Dockerfile             # Container image definition
└── k8s/
    └── deployment.yaml    # Kubernetes Deployment + Service
```

## License

See repository for license details.
