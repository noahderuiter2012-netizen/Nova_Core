# programming_agent.py
# Nova Programming Agent
# Part 1 of 1 — keep this entire file together.

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.request

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_API = "https://api.github.com"

WORKSPACE_DIR = Path(
    os.getenv("NOVA_WORKSPACE_DIR", "/tmp/nova_workspace")
).resolve()

GITHUB_TOKEN = os.getenv("NOVA_GITHUB_TOKEN", "").strip()

ALLOWED_REPOS = {
    item.strip()
    for item in os.getenv(
        "NOVA_GITHUB_ALLOWED_REPOS", ""
    ).split(",")
    if item.strip()
}

MAX_FILE_SIZE = 200_000
MAX_PROPOSAL_SIZE = 100_000

BLOCKED_PARTS = {
    ".git",
    ".env",
    "__pycache__",
    "node_modules",
    ".ssh",
    ".aws",
}

BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}

PROTECTED_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}

router = APIRouter(
    prefix="/programming",
    tags=["Nova Programming Agent"],
)

# Pending proposals are held in memory.
# They disappear when the server restarts.
PENDING_PROPOSALS: dict[str, dict[str, Any]] = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class FileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class ProposalRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=MAX_PROPOSAL_SIZE)
    reason: str = Field(default="", max_length=2000)


class GitHubFileRequest(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    path: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", max_length=200)


class GitHubProposalRequest(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=MAX_PROPOSAL_SIZE)
    message: str = Field(min_length=1, max_length=200)
    branch: str = Field(default="main", max_length=200)


class GenerateRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    language: str = Field(default="python", max_length=50)
    existing_code: str = Field(default="", max_length=30000)


class CheckRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50000)
    language: str = Field(default="python", max_length=50)


class ApproveRequest(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=100)


# ============================================================
# PATH AND SECURITY HELPERS
# ============================================================

def validate_relative_path(value: str) -> str:
    """Reject absolute paths, traversal and sensitive filenames."""

    value = value.replace("\\", "/").strip()

    if not value:
        raise HTTPException(400, "A file path is required.")

    if value.startswith("/") or "\x00" in value:
        raise HTTPException(400, "Invalid file path.")

    parts = value.split("/")

    if any(part in ("", ".", "..") for part in parts):
        raise HTTPException(400, "Invalid path component.")

    for part in parts:
        if part.lower() in {
            name.lower() for name in BLOCKED_PARTS
        }:
            raise HTTPException(
                403, "Access to this path is blocked."
            )

        if part.lower() in {
            name.lower() for name in BLOCKED_NAMES
        }:
            raise HTTPException(
                403, "Access to sensitive files is blocked."
            )

        if Path(part).suffix.lower() in PROTECTED_SUFFIXES:
            raise HTTPException(
                403, "Access to sensitive files is blocked."
            )

    return "/".join(parts)


def workspace_path(value: str) -> Path:
    relative = validate_relative_path(value)
    target = (WORKSPACE_DIR / relative).resolve()

    try:
        target.relative_to(WORKSPACE_DIR)
    except ValueError:
        raise HTTPException(403, "Path escapes the workspace.")

    # Do not allow a symlink to redirect outside the workspace.
    current = WORKSPACE_DIR

    for part in relative.split("/"):
        current = current / part

        if current.is_symlink():
            raise HTTPException(
                403, "Symbolic links are not permitted."
            )

    return target


def file_digest(content: str | None) -> str | None:
    if content is None:
        return None

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def read_text_file(path: Path) -> str:
    if not path.exists():
        raise HTTPException(404, "File not found.")

    if not path.is_file():
        raise HTTPException(400, "Path is not a file.")

    if path.stat().st_size > MAX_FILE_SIZE:
        raise HTTPException(413, "File is too large.")

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            415, "This endpoint supports text files only."
        )


def require_github_config(repo: str) -> None:
    if not GITHUB_TOKEN:
        raise HTTPException(
            503, "NOVA_GITHUB_TOKEN is not configured."
        )

    if not ALLOWED_REPOS:
        raise HTTPException(
            503, "NOVA_GITHUB_ALLOWED_REPOS is not configured."
        )

    if repo not in ALLOWED_REPOS:
        raise HTTPException(
            403, "This GitHub repository is not allowlisted."
        )

    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
    ):
        raise HTTPException(400, "Invalid repository name.")


def github_request(
    method: str,
    endpoint: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call GitHub's REST API without exposing the token."""

    url = f"{GITHUB_API}{endpoint}"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Nova-Programming-Agent",
    }

    data = None

    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(
            request, timeout=20
        ) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    except urllib.error.HTTPError as exc:
        # Do not return authorization headers or the token.
        if exc.code == 404:
            raise HTTPException(
                404, "GitHub resource not found."
            )

        if exc.code in (401, 403):
            raise HTTPException(
                exc.code,
                "GitHub rejected the request. Check token permissions.",
            )

        if exc.code == 409:
            raise HTTPException(
                409, "GitHub reported a conflict."
            )

        raise HTTPException(
            502, f"GitHub API request failed ({exc.code})."
        )

    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(
            502, "Could not connect to GitHub."
        )

    except json.JSONDecodeError:
        raise HTTPException(
            502, "GitHub returned an invalid response."
        )


def github_get_file(
    repo: str, path: str, branch: str
) -> dict[str, Any]:
    path = validate_relative_path(path)

    from urllib.parse import quote

    encoded_path = quote(path, safe="/")
    encoded_branch = quote(branch, safe="")

    result = github_request(
        "GET",
        f"/repos/{repo}/contents/{encoded_path}"
        f"?ref={encoded_branch}",
    )

    if result.get("type") != "file":
        raise HTTPException(
            400, "The requested GitHub resource is not a file."
        )

    import base64

    try:
        content = base64.b64decode(
            result.get("content", "")
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(
            415, "GitHub file is not supported text."
        )

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        raise HTTPException(413, "File is too large.")

    return {
        "path": path,
        "content": content,
        "sha": result.get("sha"),
        "branch": branch,
    }


# ============================================================
# PROPOSAL HELPERS
# ============================================================

def create_proposal(
    *,
    kind: str,
    target: str,
    new_content: str,
    old_content: str | None,
    metadata: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    if len(new_content.encode("utf-8")) > MAX_PROPOSAL_SIZE:
        raise HTTPException(
            413, "Proposed content is too large."
        )

    proposal_id = secrets.token_urlsafe(18)

    old_lines = (old_content or "").splitlines(
        keepends=True
    )
    new_lines = new_content.splitlines(keepends=True)

    diff = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"before/{target}",
            tofile=f"after/{target}",
        )
    )

    proposal = {
        "proposal_id": proposal_id,
        "kind": kind,
        "target": target,
        "content": new_content,
        "old_digest": file_digest(old_content),
        "reason": reason,
        "diff": diff[:30000],
        "metadata": metadata,
    }

    PENDING_PROPOSALS[proposal_id] = proposal

    return {
        "proposal_id": proposal_id,
        "kind": kind,
        "target": target,
        "reason": reason,
        "diff": proposal["diff"],
        "approval_required": True,
    }


def get_proposal(proposal_id: str, kind: str) -> dict:
    proposal = PENDING_PROPOSALS.get(proposal_id)

    if not proposal or proposal["kind"] != kind:
        raise HTTPException(
            404, "Proposal not found or already handled."
        )

    return proposal

# ============================================================
# CHAT PROGRAMMING WORKFLOW
# ============================================================

def is_programming_request(message: str) -> bool:
    """Recognize explicit requests to build software."""
    text = message.lower()

    build_intent = re.search(
        r"\b(build|create|make|write|generate|implement|develop)\b",
        text,
    )
    programming_topic = re.search(
        r"\b(program|code|script|app|application|website|"
        r"game|calculator|software|python|javascript|html|"
        r"css|api|bot|tool)\b",
        text,
    )

    return bool(build_intent and programming_topic)


def run_programming_workflow(task: str, client) -> dict:
    """
    Generate Python, validate syntax, and propose a workspace file.
    Never executes generated code or writes before approval.
    """
    if client is None:
        raise HTTPException(
            503, "Nova's language model is unavailable."
        )

    plan = [
        "Understand the programming request.",
        "Generate Python source code.",
        "Validate syntax without executing the program.",
        "Prepare a workspace file proposal.",
        "Wait for explicit approval before writing.",
    ]

    prompt = (
        "Write a complete, safe Python program for this request. "
        "Return source code only, without Markdown fences. "
        "Do not claim to run the program or save a file. "
        "Avoid destructive actions and credential access.\n\n"
        f"Request: {task}"
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Nova's programming agent. "
                        "Return complete Python source code only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=3000,
        )
        answer = response.choices[0].message.content or ""
    except Exception:
        raise HTTPException(
            502, "Code generation failed. No file was proposed."
        )

    match = re.search(
        r"```(?:python|py)?\s*(.*?)```",
        answer,
        flags=re.IGNORECASE | re.DOTALL,
    )
    source = (match.group(1) if match else answer).strip() + "\n"

    if not source.strip():
        raise HTTPException(
            502, "Nova returned empty code."
        )

    if len(source.encode("utf-8")) > MAX_PROPOSAL_SIZE:
        raise HTTPException(
            413, "Generated code is too large."
        )

    try:
        ast.parse(source)
    except SyntaxError as exc:
        return {
            "status": "validation_failed",
            "valid_syntax": False,
            "message": exc.msg,
            "line": exc.lineno,
            "execution_performed": False,
            "file_written": False,
            "repository_changed": False,
            "plan": plan,
        }

    task_lower = task.lower()
    if "calculator" in task_lower:
        filename = "calculator.py"
    elif "guessing game" in task_lower:
        filename = "guessing_game.py"
    elif "todo" in task_lower or "to-do" in task_lower:
        filename = "todo_app.py"
    elif "quiz" in task_lower:
        filename = "quiz.py"
    elif "timer" in task_lower:
        filename = "timer.py"
    else:
        filename = "nova_program.py"

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    index = 2

    while workspace_path(filename).exists():
        filename = f"{stem}_{index}.py"
        index += 1
        if index > 100:
            raise HTTPException(
                409, "Could not find an unused filename."
            )

    proposal = create_proposal(
        kind="workspace",
        target=filename,
        new_content=source,
        old_content=None,
        metadata={
            "path": filename,
            "expected_exists": False,
            "source": "chat_programming_workflow",
        },
        reason="Generated from chat; review before writing.",
    )

    return {
        "status": "proposal_created",
        "language": "python",
        "path": filename,
        "valid_syntax": True,
        "validation_message": "Python syntax looks valid.",
        "execution_performed": False,
        "file_written": False,
        "repository_changed": False,
        "plan": plan,
        "code": source,
        "proposal": proposal,
    }


# ============================================================
# ROUTER FACTORY
# ============================================================

def create_programming_router(
    require_access,
    get_session_id,
    client,
):
    """
    Creates Nova's programming routes.

    require_access: existing Nova access dependency.
    get_session_id: existing Nova session helper.
    client: existing model client.
    """

    programming_router = APIRouter(
        prefix="/programming",
        tags=["Nova Programming Agent"],
        dependencies=[Depends(require_access)],
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    @programming_router.get("/status")
    def programming_status():
        return {
            "status": "online",
            "agent": "Nova Programming Agent",
            "workspace": str(WORKSPACE_DIR),
            "workspace_exists": WORKSPACE_DIR.exists(),
            "github_configured": bool(GITHUB_TOKEN),
            "github_allowlist_configured": bool(ALLOWED_REPOS),
            "allowed_repositories": sorted(ALLOWED_REPOS),
            "pending_proposals": len(PENDING_PROPOSALS),
            "execution_enabled": False,
        }

    # --------------------------------------------------------
    # WORKSPACE FILE LIST
    # --------------------------------------------------------

    @programming_router.get("/workspace/files")
    def workspace_files():
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

        files = []

        for path in WORKSPACE_DIR.rglob("*"):
            if not path.is_file():
                continue

            try:
                relative = path.relative_to(
                    WORKSPACE_DIR
                ).as_posix()

                validate_relative_path(relative)

                if path.stat().st_size <= MAX_FILE_SIZE:
                    files.append({
                        "path": relative,
                        "size": path.stat().st_size,
                    })
            except (HTTPException, OSError):
                continue

            if len(files) >= 500:
                break

        return {"files": files}

    # --------------------------------------------------------
    # WORKSPACE READ
    # --------------------------------------------------------

    @programming_router.post("/workspace/read")
    def workspace_read(data: FileRequest):
        target = workspace_path(data.path)

        return {
            "path": data.path,
            "content": read_text_file(target),
        }

    # --------------------------------------------------------
    # WORKSPACE PROPOSAL
    # --------------------------------------------------------

    @programming_router.post("/workspace/proposals")
    def workspace_proposal(data: ProposalRequest):
        target = workspace_path(data.path)

        old_content = (
            read_text_file(target)
            if target.exists()
            else None
        )

        proposal = create_proposal(
            kind="workspace",
            target=data.path,
            new_content=data.content,
            old_content=old_content,
            metadata={
                "path": data.path,
                "expected_exists": target.exists(),
            },
            reason=data.reason,
        )

        return proposal

    # --------------------------------------------------------
    # APPROVE WORKSPACE PROPOSAL
    # --------------------------------------------------------

    @programming_router.post(
        "/workspace/proposals/{proposal_id}/approve"
    )
    def approve_workspace_proposal(proposal_id: str):
        proposal = get_proposal(proposal_id, "workspace")
        target = workspace_path(proposal["metadata"]["path"])

        current_content = (
            read_text_file(target)
            if target.exists()
            else None
        )

        if file_digest(current_content) != proposal["old_digest"]:
            del PENDING_PROPOSALS[proposal_id]

            raise HTTPException(
                409,
                "The file changed since the proposal was created. "
                "Create a new proposal.",
            )

        target.parent.mkdir(parents=True, exist_ok=True)

        # Write to a temporary file in the destination directory,
        # then replace the target atomically.
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(target.parent),
                delete=False,
            ) as handle:
                handle.write(proposal["content"])
                temp_path = Path(handle.name)

            os.replace(temp_path, target)

        except OSError:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

            raise HTTPException(
                500, "Could not write the approved file."
            )

        del PENDING_PROPOSALS[proposal_id]

        return {
            "status": "approved_and_written",
            "path": proposal["metadata"]["path"],
        }

    # --------------------------------------------------------
    # GITHUB READ
    # --------------------------------------------------------

    @programming_router.post("/github/read")
    def github_read(data: GitHubFileRequest):
        require_github_config(data.repo)

        result = github_get_file(
            data.repo, data.path, data.branch
        )

        return {
            "repo": data.repo,
            **result,
        }

    
    # --------------------------------------------------------
    # GITHUB PROPOSAL
    # --------------------------------------------------------

    @programming_router.post("/github/proposals")
    def github_proposal(data: GitHubProposalRequest):
        # Support creating a new file or updating an existing one.
        try:
            existing = github_get_file(
                data.repo, data.path, data.branch
            )
            old_content = existing["content"]
            expected_exists = True
            current_sha = existing["sha"]
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

            old_content = None
            expected_exists = False
            current_sha = None

        return create_proposal(
            kind="github",
            target=f"{data.repo}/{data.path}",
            new_content=data.content,
            old_content=old_content,
            metadata={
                "repo": data.repo,
                "path": data.path,
                "branch": data.branch,
                "sha": current_sha,
                "expected_exists": expected_exists,
                "message": data.message,
            },
            reason=(
                "Create a new GitHub file; approval is required."
                if not expected_exists
                else "Update an existing GitHub file; approval is required."
            ),
        )
        
    # --------------------------------------------------------
    # APPROVE GITHUB PROPOSAL
    # --------------------------------------------------------

    @programming_router.post(
        "/github/proposals/{proposal_id}/approve"
    )
    def approve_github_proposal(proposal_id: str):
        proposal = get_proposal(proposal_id, "github")
        meta = proposal["metadata"]

        require_github_config(meta["repo"])

        expected_exists = meta.get("expected_exists", True)

        # Check that the file has not changed since proposal creation.
        if expected_exists:
            current = github_get_file(
                meta["repo"],
                meta["path"],
                meta["branch"],
            )

            if current["sha"] != meta["sha"]:
                del PENDING_PROPOSALS[proposal_id]
                raise HTTPException(
                    409,
                    "The GitHub file changed since the proposal "
                    "was created. Create a new proposal.",
                )

        else:
            try:
                github_get_file(
                    meta["repo"],
                    meta["path"],
                    meta["branch"],
                )

            except HTTPException as exc:
                if exc.status_code != 404:
                    raise

            else:
                del PENDING_PROPOSALS[proposal_id]
                raise HTTPException(
                    409,
                    "A GitHub file with this path now exists. "
                    "Create a new update proposal.",
                )

        import base64
        from urllib.parse import quote

        encoded_path = quote(meta["path"], safe="/")

        body = {
            "message": meta["message"],
            "content": base64.b64encode(
                proposal["content"].encode("utf-8")
            ).decode("ascii"),
            "branch": meta["branch"],
        }

        # Existing files require their current SHA.
        if expected_exists:
            body["sha"] = meta["sha"]

        result = github_request(
            "PUT",
            f"/repos/{meta['repo']}/contents/{encoded_path}",
            body,
        )

        del PENDING_PROPOSALS[proposal_id]

        return {
            "status": "committed",
            "repo": meta["repo"],
            "path": meta["path"],
            "commit_sha": result.get(
                "commit", {}
            ).get("sha"),
            "html_url": result.get(
                "commit", {}
            ).get("html_url"),
            "file_url": result.get(
                "content", {}
            ).get("html_url"),
        }

    # --------------------------------------------------------
    # CHECK CODE
    # --------------------------------------------------------

    @programming_router.post("/check")
    def check_code(data: CheckRequest):
        language = data.language.lower().strip()

        # Validate Python without executing it.
        if language in {"python", "py"}:
            try:
                ast.parse(data.code)

                return {
                    "language": "python",
                    "valid_syntax": True,
                    "message": "Python syntax looks valid.",
                    "execution_performed": False,
                }

            except SyntaxError as exc:
                return {
                    "language": "python",
                    "valid_syntax": False,
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.offset,
                    "execution_performed": False,
                }

        # Validate JSON.
        if language == "json":
            try:
                json.loads(data.code)

                return {
                    "language": "json",
                    "valid_syntax": True,
                    "message": "JSON syntax looks valid.",
                    "execution_performed": False,
                }

            except json.JSONDecodeError as exc:
                return {
                    "language": "json",
                    "valid_syntax": False,
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                    "execution_performed": False,
                }

        raise HTTPException(
            status_code=400,
            detail=(
                "Syntax validation is supported for "
                "Python and JSON only."
            ),
        )

    # --------------------------------------------------------
    # RETURN ROUTER
    # --------------------------------------------------------

    return programming_router
    
                
                if language in {"python", "py"}:
            try:
                ast.parse(data.code)
                return {
                    "language": "python",
                    "valid_syntax": True,
                    "message": "Python syntax looks valid.",
                    "execution_performed": False,
                }
            except SyntaxError as exc:
                return {
                    "language": "python",
                    "valid_syntax": False,
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.offset,
                    "execution_performed": False,
                }

    # --------------------------------------------------------
    # APPROVE GITHUB PROPOSAL
    # --------------------------------------------------------

    @programming_router.post(
        "/github/proposals/{proposal_id}/approve"
    )
    def approve_github_proposal(proposal_id: str):
        proposal = get_proposal(proposal_id, "github")
        meta = proposal["metadata"]

        require_github_config(meta["repo"])

        expected_exists = meta.get("expected_exists", True)

        # Re-check the file immediately before committing.
        if expected_exists:
            current = github_get_file(
                meta["repo"],
                meta["path"],
                meta["branch"],
            )

            if current["sha"] != meta["sha"]:
                del PENDING_PROPOSALS[proposal_id]
                raise HTTPException(
                    409,
                    "The GitHub file changed since the proposal "
                    "was created. Create a new proposal.",
                )
        else:
            try:
                github_get_file(
                    meta["repo"],
                    meta["path"],
                    meta["branch"],
                )
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            else:
                del PENDING_PROPOSALS[proposal_id]
                raise HTTPException(
                    409,
                    "A GitHub file with this path now exists. "
                    "Create a new update proposal.",
                )

        import base64
        from urllib.parse import quote

        encoded_path = quote(meta["path"], safe="/")

        body = {
            "message": meta["message"],
            "content": base64.b64encode(
                proposal["content"].encode("utf-8")
            ).decode("ascii"),
            "branch": meta["branch"],
        }

        # GitHub requires a SHA for updates, but not new files.
        if expected_exists:
            body["sha"] = meta["sha"]

        result = github_request(
            "PUT",
            f"/repos/{meta['repo']}/contents/{encoded_path}",
            body,
        )

        del PENDING_PROPOSALS[proposal_id]

        return {
            "status": "committed",
            "repo": meta["repo"],
            "path": meta["path"],
            "commit_sha": result.get(
                "commit", {}
            ).get("sha"),
            "html_url": result.get(
                "commit", {}
            ).get("html_url"),
        }

    # --------------------------------------------------------
    # CHECK CODE
    # --------------------------------------------------------

    @programming_router.post("/check")
    def check_code(data: CheckRequest):
        language = data.language.lower().strip()

        # Validate Python without executing it.
        if language in {"python", "py"}:
            try:
                ast.parse(data.code)

                return {
                    "language": "python",
                    "valid_syntax": True,
                    "message": "Python syntax looks valid.",
                    "execution_performed": False,
                }

            except SyntaxError as exc:
                return {
                    "language": "python",
                    "valid_syntax": False,
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.offset,
                    "execution_performed": False,
                }

        # Validate JSON.
        if language == "json":
            try:
                json.loads(data.code)

                return {
                    "language": "json",
                    "valid_syntax": True,
                    "message": "JSON syntax looks valid.",
                    "execution_performed": False,
                }

            except json.JSONDecodeError as exc:
                return {
                    "language": "json",
                    "valid_syntax": False,
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                    "execution_performed": False,
                }

        # Reject languages that are not supported.
        raise HTTPException(
            status_code=400,
            detail=(
                "Syntax validation is supported for "
                "Python and JSON only."
            ),
        )

    # --------------------------------------------------------
    # RETURN ROUTER
    # --------------------------------------------------------

    return programming_router

