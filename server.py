"""
Git MCP Server with Streamable HTTP transport.

Wraps the official mcp-server-git tools and adds a git_clone tool
for dynamic repository cloning from Gitea or any Git remote.
"""

import contextlib
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import git
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from mcp_server_git.server import (
    DEFAULT_CONTEXT_LINES,
    GitAdd,
    GitBranch,
    GitCheckout,
    GitCommit,
    GitCreateBranch,
    GitDiff,
    GitDiffStaged,
    GitDiffUnstaged,
    GitLog,
    GitReset,
    GitShow,
    GitStatus,
    GitTools,
    git_add,
    git_branch,
    git_checkout,
    git_commit,
    git_create_branch,
    git_diff,
    git_diff_staged,
    git_diff_unstaged,
    git_log,
    git_reset,
    git_show,
    git_status,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REPOS_DIR = Path(os.environ.get("REPOS_DIR", "/repos"))
REPOS_DIR.mkdir(parents=True, exist_ok=True)

SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def repo_name_from_url(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def main(port: int = 8080) -> int:
    app = Server("git-mcp-server")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="git_clone",
                description=(
                    "Clone a Git repository from a remote URL into the local workspace. "
                    "Returns the local path. Use this before other git tools if the repo "
                    "is not yet cloned. Already-cloned repos are pulled instead."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Git remote URL to clone (e.g. http://infra.5g-deployment.lab:3000/student/ztp-repository.git)",
                        },
                        "branch": {
                            "type": "string",
                            "description": "Optional branch to checkout after cloning",
                        },
                        "username": {
                            "type": "string",
                            "description": "Optional username for HTTP auth (embedded in remote URL for push)",
                        },
                        "password": {
                            "type": "string",
                            "description": "Optional password for HTTP auth",
                        },
                    },
                },
            ),
            Tool(
                name="git_list_repos",
                description="List all Git repositories currently cloned in the workspace.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="git_list_files",
                description=(
                    "List files and directories in a Git repository. "
                    "Returns a tree-like listing. Use this to explore repo structure before reading files."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["repo_path"],
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to the Git repository"},
                        "path": {"type": "string", "description": "Optional subdirectory path relative to repo root (default: root)"},
                        "recursive": {"type": "boolean", "description": "If true, list all files recursively (default: false)"},
                        "pattern": {"type": "string", "description": "Optional glob pattern to filter files (e.g. '*.yaml', '**/*policy*')"},
                    },
                },
            ),
            Tool(
                name="git_read_file",
                description=(
                    "Read the contents of a file from a Git repository. "
                    "Returns the full file content as text."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["repo_path", "file_path"],
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to the Git repository"},
                        "file_path": {"type": "string", "description": "File path relative to the repo root"},
                        "revision": {"type": "string", "description": "Optional git revision (branch/tag/commit) to read from. Defaults to working tree."},
                    },
                },
            ),
            Tool(
                name="git_search_files",
                description=(
                    "Search for a text pattern in files within a Git repository (like grep). "
                    "Returns matching lines with file paths and line numbers."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["repo_path", "pattern"],
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to the Git repository"},
                        "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                        "path": {"type": "string", "description": "Optional subdirectory or file to restrict search to"},
                        "ignore_case": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
                    },
                },
            ),
            Tool(
                name="git_write_file",
                description=(
                    "Write or overwrite a file in a Git repository. "
                    "Creates parent directories if needed. Use git_add and git_commit afterwards to save changes."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["repo_path", "file_path", "content"],
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to the Git repository"},
                        "file_path": {"type": "string", "description": "File path relative to the repo root"},
                        "content": {"type": "string", "description": "Full file content to write"},
                    },
                },
            ),
            Tool(
                name="git_edit_file",
                description=(
                    "Edit a file in a Git repository by replacing a specific text segment. "
                    "Finds the exact old_text and replaces it with new_text. "
                    "Use git_read_file first to see the current content."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["repo_path", "file_path", "old_text", "new_text"],
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to the Git repository"},
                        "file_path": {"type": "string", "description": "File path relative to the repo root"},
                        "old_text": {"type": "string", "description": "Exact text to find and replace (must match uniquely)"},
                        "new_text": {"type": "string", "description": "Replacement text"},
                    },
                },
            ),
            Tool(
                name="git_push",
                description=(
                    "Push committed changes to the remote repository. "
                    "Pushes the current branch to origin by default."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["repo_path"],
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to the Git repository"},
                        "remote": {"type": "string", "description": "Remote name (default: origin)"},
                        "branch": {"type": "string", "description": "Branch to push (default: current branch)"},
                    },
                },
            ),
            Tool(name=GitTools.STATUS, description="Shows the working tree status", inputSchema=GitStatus.model_json_schema()),
            Tool(name=GitTools.DIFF_UNSTAGED, description="Shows changes in the working directory that are not yet staged", inputSchema=GitDiffUnstaged.model_json_schema()),
            Tool(name=GitTools.DIFF_STAGED, description="Shows changes that are staged for commit", inputSchema=GitDiffStaged.model_json_schema()),
            Tool(name=GitTools.DIFF, description="Shows differences between branches or commits", inputSchema=GitDiff.model_json_schema()),
            Tool(name=GitTools.COMMIT, description="Records changes to the repository", inputSchema=GitCommit.model_json_schema()),
            Tool(name=GitTools.ADD, description="Adds file contents to the staging area", inputSchema=GitAdd.model_json_schema()),
            Tool(name=GitTools.RESET, description="Unstages all staged changes", inputSchema=GitReset.model_json_schema()),
            Tool(name=GitTools.LOG, description="Shows the commit logs", inputSchema=GitLog.model_json_schema()),
            Tool(name=GitTools.CREATE_BRANCH, description="Creates a new branch from an optional base branch", inputSchema=GitCreateBranch.model_json_schema()),
            Tool(name=GitTools.CHECKOUT, description="Switches branches", inputSchema=GitCheckout.model_json_schema()),
            Tool(name=GitTools.SHOW, description="Shows the contents of a commit", inputSchema=GitShow.model_json_schema()),
            Tool(name=GitTools.BRANCH, description="List Git branches", inputSchema=GitBranch.model_json_schema()),
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "git_clone":
            return _handle_clone(arguments)

        if name == "git_list_repos":
            return _handle_list_repos()

        if name == "git_list_files":
            return _handle_list_files(arguments)

        if name == "git_read_file":
            return _handle_read_file(arguments)

        if name == "git_search_files":
            return _handle_search_files(arguments)

        if name == "git_push":
            return _handle_push(arguments)

        if name == "git_write_file":
            return _handle_write_file(arguments)

        if name == "git_edit_file":
            return _handle_edit_file(arguments)

        repo_path = Path(arguments["repo_path"])
        repo = git.Repo(repo_path)

        match name:
            case GitTools.STATUS:
                result = git_status(repo)
                return [TextContent(type="text", text=f"Repository status:\n{result}")]
            case GitTools.DIFF_UNSTAGED:
                result = git_diff_unstaged(repo, arguments.get("context_lines", DEFAULT_CONTEXT_LINES))
                return [TextContent(type="text", text=f"Unstaged changes:\n{result}")]
            case GitTools.DIFF_STAGED:
                result = git_diff_staged(repo, arguments.get("context_lines", DEFAULT_CONTEXT_LINES))
                return [TextContent(type="text", text=f"Staged changes:\n{result}")]
            case GitTools.DIFF:
                result = git_diff(repo, arguments["target"], arguments.get("context_lines", DEFAULT_CONTEXT_LINES))
                return [TextContent(type="text", text=f"Diff with {arguments['target']}:\n{result}")]
            case GitTools.COMMIT:
                result = git_commit(repo, arguments["message"])
                return [TextContent(type="text", text=result)]
            case GitTools.ADD:
                result = git_add(repo, arguments["files"])
                return [TextContent(type="text", text=result)]
            case GitTools.RESET:
                result = git_reset(repo)
                return [TextContent(type="text", text=result)]
            case GitTools.LOG:
                log = git_log(repo, arguments.get("max_count", 10), arguments.get("start_timestamp"), arguments.get("end_timestamp"))
                return [TextContent(type="text", text="Commit history:\n" + "\n".join(log))]
            case GitTools.CREATE_BRANCH:
                result = git_create_branch(repo, arguments["branch_name"], arguments.get("base_branch"))
                return [TextContent(type="text", text=result)]
            case GitTools.CHECKOUT:
                result = git_checkout(repo, arguments["branch_name"])
                return [TextContent(type="text", text=result)]
            case GitTools.SHOW:
                result = git_show(repo, arguments["revision"])
                return [TextContent(type="text", text=result)]
            case GitTools.BRANCH:
                result = git_branch(repo, arguments.get("branch_type", "local"), arguments.get("contains"), arguments.get("not_contains"))
                return [TextContent(type="text", text=result)]
            case _:
                raise ValueError(f"Unknown tool: {name}")

    def _embed_credentials(url: str, username: str | None, password: str | None) -> str:
        if not username:
            return url
        from urllib.parse import urlparse, urlunparse, quote
        parsed = urlparse(url)
        userinfo = quote(username, safe="")
        if password:
            userinfo += ":" + quote(password, safe="")
        netloc = f"{userinfo}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))

    def _handle_clone(arguments: dict) -> list[TextContent]:
        url = arguments["url"]
        branch = arguments.get("branch")
        username = arguments.get("username")
        password = arguments.get("password")
        repo_name = repo_name_from_url(url)

        if not SAFE_NAME_RE.match(repo_name):
            return [TextContent(type="text", text=f"Error: unsafe repository name derived from URL: {repo_name}")]

        auth_url = _embed_credentials(url, username, password)
        dest = REPOS_DIR / repo_name

        if dest.exists():
            try:
                repo = git.Repo(dest)
                if username:
                    repo.remotes.origin.set_url(auth_url)
                repo.remotes.origin.pull()
                msg = f"Repository already exists at {dest}, pulled latest changes."
                if branch:
                    repo.git.checkout(branch)
                    msg += f" Checked out branch '{branch}'."
                return [TextContent(type="text", text=msg)]
            except Exception as e:
                return [TextContent(type="text", text=f"Repo exists at {dest} but pull failed: {e}")]

        try:
            kwargs = {}
            if branch:
                kwargs["branch"] = branch
            git.Repo.clone_from(auth_url, str(dest), **kwargs)
            return [TextContent(type="text", text=f"Cloned {url} to {dest}. Use repo_path='{dest}' for other git tools.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Clone failed: {e}")]

    def _handle_list_repos() -> list[TextContent]:
        repos = []
        for p in sorted(REPOS_DIR.iterdir()):
            if p.is_dir() and (p / ".git").is_dir():
                try:
                    repo = git.Repo(p)
                    branch = repo.active_branch.name
                    repos.append(f"  {p}  (branch: {branch})")
                except Exception:
                    repos.append(f"  {p}  (detached/unknown)")
        if not repos:
            return [TextContent(type="text", text="No repositories cloned yet. Use git_clone to clone one.")]
        return [TextContent(type="text", text="Cloned repositories:\n" + "\n".join(repos))]

    def _handle_list_files(arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        sub_path = arguments.get("path", "")
        recursive = arguments.get("recursive", False)
        pattern = arguments.get("pattern")

        try:
            repo = git.Repo(repo_path)
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

        target = repo_path / sub_path if sub_path else repo_path

        if not target.exists():
            return [TextContent(type="text", text=f"Path not found: {sub_path}")]

        if pattern:
            import fnmatch
            matches = []
            for f in sorted(target.rglob("*")):
                if f.is_file() and not ".git" in f.parts:
                    rel = f.relative_to(repo_path)
                    if fnmatch.fnmatch(str(rel), pattern) or fnmatch.fnmatch(f.name, pattern):
                        matches.append(str(rel))
            if not matches:
                return [TextContent(type="text", text=f"No files matching '{pattern}' found.")]
            return [TextContent(type="text", text="\n".join(matches))]

        entries = []
        if recursive:
            for f in sorted(target.rglob("*")):
                if ".git" in f.parts:
                    continue
                if f.is_file():
                    entries.append(str(f.relative_to(repo_path)))
        else:
            for f in sorted(target.iterdir()):
                if f.name == ".git":
                    continue
                rel = f.relative_to(repo_path)
                entries.append(f"{rel}/" if f.is_dir() else str(rel))

        if not entries:
            return [TextContent(type="text", text="Directory is empty.")]
        return [TextContent(type="text", text="\n".join(entries))]

    def _handle_read_file(arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        file_path = arguments["file_path"]
        revision = arguments.get("revision")

        try:
            repo = git.Repo(repo_path)
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

        if revision:
            try:
                content = repo.git.show(f"{revision}:{file_path}")
                return [TextContent(type="text", text=content)]
            except Exception as e:
                return [TextContent(type="text", text=f"Error reading {file_path} at {revision}: {e}")]

        full_path = repo_path / file_path
        if not full_path.exists():
            return [TextContent(type="text", text=f"File not found: {file_path}")]
        if not full_path.is_file():
            return [TextContent(type="text", text=f"Not a file: {file_path}")]

        try:
            content = full_path.read_text(errors="replace")
            if len(content) > 100_000:
                content = content[:100_000] + f"\n\n... truncated (file is {len(content)} bytes total)"
            return [TextContent(type="text", text=content)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading file: {e}")]

    def _handle_push(arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        remote_name = arguments.get("remote", "origin")
        branch = arguments.get("branch")

        try:
            repo = git.Repo(repo_path)
            remote = repo.remote(remote_name)
            if branch:
                result = remote.push(branch)
            else:
                result = remote.push()
            summary = []
            for info in result:
                summary.append(f"{info.local_ref} -> {info.remote_ref}: {info.summary.strip()}")
            if not summary:
                return [TextContent(type="text", text=f"Pushed to {remote_name} successfully.")]
            return [TextContent(type="text", text="Push results:\n" + "\n".join(summary))]
        except Exception as e:
            return [TextContent(type="text", text=f"Push failed: {e}")]

    def _handle_write_file(arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        file_path = arguments["file_path"]
        content = arguments["content"]

        full_path = repo_path / file_path
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            return [TextContent(type="text", text=f"Wrote {len(content)} bytes to {file_path}. Use git_add and git_commit to save.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error writing file: {e}")]

    def _handle_edit_file(arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        file_path = arguments["file_path"]
        old_text = arguments["old_text"]
        new_text = arguments["new_text"]

        full_path = repo_path / file_path
        if not full_path.exists():
            return [TextContent(type="text", text=f"File not found: {file_path}")]

        try:
            content = full_path.read_text()
            count = content.count(old_text)
            if count == 0:
                return [TextContent(type="text", text=f"old_text not found in {file_path}. Use git_read_file to check current content.")]
            if count > 1:
                return [TextContent(type="text", text=f"old_text matches {count} locations in {file_path}. Provide more context to match uniquely.")]
            new_content = content.replace(old_text, new_text, 1)
            full_path.write_text(new_content)
            return [TextContent(type="text", text=f"Edited {file_path}: replaced text successfully. Use git_add and git_commit to save.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error editing file: {e}")]

    def _handle_search_files(arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        pattern = arguments["pattern"]
        sub_path = arguments.get("path", "")
        ignore_case = arguments.get("ignore_case", False)

        try:
            args = ["--line-number", "-r"]
            if ignore_case:
                args.append("-i")
            if sub_path:
                args.extend(["--", pattern, sub_path])
            else:
                args.extend(["--", pattern])

            repo = git.Repo(repo_path)
            result = repo.git.grep(*args)
            lines = result.split("\n")
            if len(lines) > 200:
                result = "\n".join(lines[:200]) + f"\n\n... truncated ({len(lines)} total matches)"
            return [TextContent(type="text", text=result)]
        except git.GitCommandError as e:
            if e.status == 1:
                return [TextContent(type="text", text=f"No matches found for '{pattern}'.")]
            return [TextContent(type="text", text=f"Search error: {e}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    session_manager = StreamableHTTPSessionManager(
        app=app,
        event_store=None,
        json_response=True,
        stateless=True,
    )

    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(starlette_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info("git-mcp-server started on port %d", port)
            yield
            logger.info("git-mcp-server shutting down")

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz),
            Mount("/mcp", app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )

    uvicorn.run(starlette_app, host="0.0.0.0", port=port)
    return 0


if __name__ == "__main__":
    main()
