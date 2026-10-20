"""Journaled, exact-path publication of validated scraper candidates.

Only the writer lock owner may call these functions. No recovery path resets a
checkout, rewrites history, or infers ownership from a filename alone.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from uuid import uuid4

from loguru import logger

from custom_modules.git_workflow import _git, GitWorkflowError
from custom_modules.updater_safety import FatalUpdaterError, atomic_copy, atomic_json, sync_directory


def digest(path: Path) -> str | None:
    if path.is_symlink():
        raise FatalUpdaterError(f'Symlink is not an updater-owned file: {path}')
    try:
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    except FileNotFoundError:
        return None


def checked_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (parts.is_absolute() or '..' in parts.parts or '\\' in relative or ':' in relative
            or not parts.parts or parts.parts[0] != 'datastores'):
        raise FatalUpdaterError('Recovery manifest contains an invalid datastore path')
    path = root / Path(*parts.parts)
    if not path.resolve().is_relative_to((root / 'datastores').resolve()):
        raise FatalUpdaterError('Recovery path escapes the datastore')
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise FatalUpdaterError('Recovery paths must not traverse symlinks')
    return path


def changed_paths(repository: Path) -> set[str]:
    fields = iter(_git(repository, 'status', '--porcelain=v1', '-z', '--untracked-files=all', strip=False).split('\0'))
    paths = set()
    for field in fields:
        if not field:
            continue
        if len(field) < 4 or any(flag in field[:2] for flag in 'RCU'):
            raise GitWorkflowError('Renames, copies, and conflicts require manual recovery')
        paths.add(field[3:])
    return paths


class Publication:
    def __init__(self, repository: Path, state_root: Path):
        self.repository = Path(repository).resolve()
        self.root = Path(state_root) / 'transaction'
        self.stage = self.root / 'candidate' / 'datastores'
        self.journal = self.root / 'journal.json'

    def prepare(self) -> None:
        if self.journal.exists():
            raise FatalUpdaterError('Pending publication must be recovered before staging')
        # This directory is exclusively updater-owned; no tracked files change.
        if self.root.exists():
            shutil.rmtree(self.root)
        self.stage.mkdir(parents=True)
        source = self.repository / 'datastores' / 'narou.sqlite'
        if not source.exists():
            raise FatalUpdaterError('Missing database; run git lfs pull or bootstrap_narou_sqlite.py')
        if source.is_symlink():
            raise FatalUpdaterError('Canonical database must not be a symlink')
        shutil.copyfile(source, self.stage / 'narou.sqlite')

    def record(self, base: str, uids: list[str]) -> dict:
        if changed_paths(self.repository) or _git(self.repository, 'rev-parse', 'HEAD') != base:
            raise FatalUpdaterError('Checkout changed while scraping; candidate retained for investigation')
        files, transaction_id = {}, uuid4().hex
        for candidate in sorted(self.stage.rglob('*')):
            if not candidate.is_file():
                continue
            relative = 'datastores/' + candidate.relative_to(self.stage).as_posix()
            original = checked_path(self.repository, relative)
            before, after = digest(original), digest(candidate)
            if before != after:
                # Windows FlushFileBuffers requires a writable handle.
                with candidate.open('r+b') as stream:
                    os.fsync(stream.fileno())
                sync_directory(candidate.parent)
                files[relative] = {'original': before, 'candidate': after,
                                   'temporary': f'{relative}.narou-{transaction_id}.tmp'}
        if not files or not uids:
            raise FatalUpdaterError('Changed scrape produced no publishable files or chapters')
        for directory in sorted((p for p in self.stage.rglob('*') if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            sync_directory(directory)
        sync_directory(self.stage)
        sync_directory(self.stage.parent)
        record = {'version': 1, 'id': transaction_id, 'base': base, 'files': files,
                  'uids': uids, 'phase': 'prepared', 'tree': None, 'commit': None}
        atomic_json(self.journal, record)
        sync_directory(self.root.parent)
        sync_directory(self.root.parent.parent)
        return record

    def load(self) -> dict:
        def require(condition):
            if not condition:
                raise ValueError('Invalid journal field')

        try:
            value = json.loads(self.journal.read_text(encoding='utf-8'))
            sha = lambda item: isinstance(item, str) and re.fullmatch(r'[0-9a-f]{40,64}', item)
            require(value['version'] == 1 and re.fullmatch(r'[0-9a-f]{32}', value['id']))
            require(sha(value['base']) and value['files'] and value['uids'])
            require(value['phase'] in {'prepared', 'promoted', 'committing', 'pushing'})
            require(value['tree'] is None or sha(value['tree']))
            require(value['commit'] is None or sha(value['commit']))
            require(all(isinstance(uid, str) and uid.isdigit() for uid in value['uids']))
            for relative, hashes in value['files'].items():
                checked_path(self.repository, relative)
                require(hashes['original'] is None or re.fullmatch(r'[0-9a-f]{64}', hashes['original']))
                require(re.fullmatch(r'[0-9a-f]{64}', hashes['candidate']))
                require(hashes['temporary'] == f'{relative}.narou-{value["id"]}.tmp')
                checked_path(self.repository, hashes['temporary'])
            return value
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise FatalUpdaterError('Corrupt publication journal; preserve state and repair manually') from exc

    def finish(self, record: dict | None = None, *, phase=lambda value: None,
               published=lambda commit: None) -> int:
        record = self.load() if record is None else record
        log = logger.bind(transaction_id=record['id'])
        allowed = set(record['files'])
        temporaries = {hashes['temporary'] for hashes in record['files'].values()}
        if _git(self.repository, 'rev-parse', '--abbrev-ref', 'HEAD') != 'master':
            raise FatalUpdaterError('Recovery requires the original master checkout')
        if changed_paths(self.repository) - allowed - temporaries:
            raise FatalUpdaterError('Unrelated checkout changes block recovery; preserve journal and inspect git status')
        head = _git(self.repository, 'rev-parse', 'HEAD')
        if head == record['base']:
            if record['commit']:
                raise FatalUpdaterError('Recorded commit disappeared; refusing to rewrite history')
            phase('promoting')
            log.bind(phase='promoting').info('Resuming verified publication')
            # Verify the entire set before overwriting any destination.
            for relative, hashes in record['files'].items():
                target = checked_path(self.repository, relative)
                temporary = checked_path(self.repository, hashes['temporary'])
                if temporary.exists() and not temporary.is_file():
                    raise FatalUpdaterError('Promotion temporary is not a regular file')
                if digest(target) not in (hashes['original'], hashes['candidate']):
                    raise FatalUpdaterError(f'Unowned modification blocks recovery: {relative}')
                candidate = checked_path(self.root / 'candidate', relative)
                if digest(candidate) != hashes['candidate']:
                    raise FatalUpdaterError(f'Candidate missing or corrupt: {relative}')
            # A user can stage different bytes, then restore the original
            # worktree. Verify index ownership as well before git add replaces it.
            staged_paths = set(_git(self.repository, 'diff', '--cached', '--name-only', '-z', strip=False).split('\0')) - {''}
            for relative in staged_paths:
                if relative not in allowed:
                    raise FatalUpdaterError('Unrelated staged changes block recovery')
                index_entry = _git(self.repository, 'ls-files', '--stage', '--', relative).split('\t', 1)[0].split()
                original_entry = _git(self.repository, 'ls-tree', record['base'], '--', relative).split('\t', 1)[0].split()
                expected = {('100644', _git(self.repository, 'hash-object', f'--path={relative}',
                                           str(checked_path(self.root / 'candidate', relative))))}
                if original_entry:
                    expected.add((original_entry[0], original_entry[2]))
                if len(index_entry) != 3 or (index_entry[0], index_entry[1]) not in expected or index_entry[2] != '0':
                    raise FatalUpdaterError(f'Unowned staged content blocks recovery: {relative}')
            for relative, hashes in record['files'].items():
                target = checked_path(self.repository, relative)
                temporary = checked_path(self.repository, hashes['temporary'])
                # The journal reserves this unique transaction path before any
                # copy starts. A killed copy can leave partial bytes here.
                temporary.unlink(missing_ok=True)
                if digest(target) != hashes['candidate']:
                    atomic_copy(checked_path(self.root / 'candidate', relative), target, temporary=temporary)
            for directory in sorted({checked_path(self.repository, p).parent for p in allowed},
                                    key=lambda p: len(p.parts), reverse=True):
                sync_directory(directory)
            sync_directory(self.repository / 'datastores')
            record['phase'] = 'promoted'
            atomic_json(self.journal, record)
            phase('committing')
            _git(self.repository, 'add', '--', *sorted(allowed))
            if changed_paths(self.repository) - allowed:
                raise FatalUpdaterError('Checkout changed during staging')
            # Reject staged changes to unrelated files, even if their worktree
            # happens to match the index. Exact tree is durable before commit.
            staged = set(_git(self.repository, 'diff', '--cached', '--name-only', '-z', strip=False).split('\0')) - {''}
            if staged != allowed:
                raise FatalUpdaterError('Git index does not exactly match the publication manifest')
            record['tree'] = _git(self.repository, 'write-tree')
            record['phase'] = 'committing'
            atomic_json(self.journal, record)
            message = f'Chapter update for {", ".join(record["uids"])}\n\nNarou-Transaction: {record["id"]}'
            _git(self.repository, 'commit', '-m', message)
            head = _git(self.repository, 'rev-parse', 'HEAD')
        # Covers a crash after git commit but before the journal write.
        if (not record['tree'] or _git(self.repository, 'rev-parse', f'{head}^') != record['base']
                or _git(self.repository, 'rev-parse', f'{head}^{{tree}}') != record['tree']
                or f'Narou-Transaction: {record["id"]}' not in _git(self.repository, 'log', '-1', '--format=%B')
                or (record['commit'] and record['commit'] != head)):
            raise FatalUpdaterError('HEAD is not the exact updater-owned publication commit')
        if changed_paths(self.repository):
            raise FatalUpdaterError('Checkout changed after publication commit')
        for relative, hashes in record['files'].items():
            if digest(checked_path(self.repository, relative)) != hashes['candidate']:
                raise FatalUpdaterError(f'Published worktree changed: {relative}')
        record.update(commit=head, phase='pushing')
        atomic_json(self.journal, record)
        phase('pushing')
        log.bind(phase='pushing').info('Publishing verified commit {}', head)
        _git(self.repository, 'fetch', '--no-tags', 'origin', '+refs/heads/master:refs/remotes/origin/master')
        remote = _git(self.repository, 'rev-parse', 'origin/master')
        ancestor = _git(self.repository, 'merge-base', head, remote)
        if ancestor == head:
            log.info('Publication already present remotely')
        elif remote == record['base']:
            _git(self.repository, 'push', 'origin', 'HEAD:refs/heads/master')
            _git(self.repository, 'fetch', '--no-tags', 'origin', '+refs/heads/master:refs/remotes/origin/master')
            if _git(self.repository, 'merge-base', head, 'origin/master') != head:
                raise FatalUpdaterError('Remote does not contain the pushed publication')
        else:
            raise FatalUpdaterError('Remote diverged during publication; manual reconciliation required')
        published(head)
        count = len(record['uids'])
        # Rename first so interruption during cleanup cannot damage an active journal.
        completed = self.root.with_name(f'completed-{record["id"]}')
        os.replace(self.root, completed)
        sync_directory(completed.parent)
        shutil.rmtree(completed)
        log.info('Published {} chapter(s) at {}', count, head)
        return count
