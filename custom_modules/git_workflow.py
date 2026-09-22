"""Small, fail-closed Git workflow for the single Narou scraper writer."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitWorkflowError(RuntimeError):
    pass


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repository, text=True, capture_output=True, check=False)
    if result.returncode:
        raise GitWorkflowError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def require_clean_current_master(repository: Path, branch: str = "master") -> None:
    """Refuse to write unless this clone exactly matches the remote branch."""
    if _git(repository, "status", "--porcelain"):
        raise GitWorkflowError("working tree is not clean")
    _git(repository, "fetch", "origin", branch)
    remote_ref = f"origin/{branch}"
    if _git(repository, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise GitWorkflowError(f"writer must run on {branch}")
    if _git(repository, "rev-parse", "HEAD") != _git(repository, "rev-parse", remote_ref):
        _git(repository, "merge", "--ff-only", remote_ref)
    if _git(repository, "rev-parse", "HEAD") != _git(repository, "rev-parse", remote_ref):
        raise GitWorkflowError("local branch is ahead of or diverged from origin; manual recovery required")


def commit_datastores_and_push(repository: Path, message: str, branch: str = "master") -> bool:
    changed = _git(repository, "status", "--porcelain", "--", "datastores")
    if not changed:
        return False
    _git(repository, "add", "--", "datastores")
    _git(repository, "commit", "-m", message)
    _git(repository, "push", "origin", branch)
    return True
