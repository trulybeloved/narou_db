"""Small, fail-closed Git workflow for the single Narou scraper writer."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path

from custom_modules.updater_safety import FatalUpdaterError, RetryableUpdaterError, kill_process_tree


class GitWorkflowError(FatalUpdaterError):
    pass


class GitTransportError(RetryableUpdaterError):
    pass


def _git(repository: Path, *args: str, strip: bool = True) -> str:
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    if not environment.get('GIT_SSH_COMMAND') and not environment.get('GIT_SSH'):
        environment['GIT_SSH_COMMAND'] = 'ssh -oBatchMode=yes -oConnectTimeout=30'
    process = subprocess.Popen(['git', *args], cwd=repository, text=True,
                               encoding='utf-8', errors='replace', stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=environment)
    try:
        stdout, stderr = process.communicate(timeout=float(os.getenv('NAROU_GIT_TIMEOUT', '120')))
    except BaseException as exc:
        kill_process_tree(process.pid)
        process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise GitTransportError(f'Git {args[0]} exceeded its deadline') from exc
        raise
    if process.returncode:
        detail = stderr.strip() or stdout.strip() or 'git command failed'
        transient = ('could not resolve', 'failed to connect', "couldn't connect", 'connection timed out',
                     'connection reset', 'connection refused', 'remote end hung up',
                     'unexpected disconnect', 'network is unreachable', 'http 429',
                     'error: 429', 'error: 500', 'error: 502', 'error: 503',
                     'error: 504', 'ssh_exchange_identification', 'early eof', 'broken pipe',
                     'internal server error', 'operation timed out', 'empty reply from server',
                     'tls connection was non-properly terminated', 'service unavailable',
                     'bad gateway', 'gateway timeout', 'temporarily unavailable', 'connection closed',
                     'connection aborted', 'unable to connect')
        error = GitTransportError if any(token in detail.lower() for token in transient) else GitWorkflowError
        # Detail stays in local logs; the health endpoint exposes categories only.
        raise error(f'Git {args[0]} failed: {detail}')
    return stdout.strip() if strip else stdout


def runtime_directory(repository: Path) -> Path:
    directory = Path(_git(repository, 'rev-parse', '--git-common-dir'))
    return (repository / directory).resolve() / 'narou-updater'


def require_clean_current_master(repository: Path, branch: str = "master") -> None:
    """Refuse to write unless this clone exactly matches the remote branch."""
    if _git(repository, "status", "--porcelain"):
        raise GitWorkflowError("working tree is not clean")
    _git(repository, 'fetch', '--no-tags', 'origin', f'+refs/heads/{branch}:refs/remotes/origin/{branch}')
    remote_ref = f"origin/{branch}"
    if _git(repository, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise GitWorkflowError(f"writer must run on {branch}")
    if _git(repository, "rev-parse", "HEAD") != _git(repository, "rev-parse", remote_ref):
        if _git(repository, 'merge-base', 'HEAD', remote_ref) != _git(repository, 'rev-parse', 'HEAD'):
            raise GitWorkflowError('local branch is ahead of or diverged from origin; manual recovery required')
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
