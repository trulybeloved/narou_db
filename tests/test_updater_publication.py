"""Crash recovery uses real temporary repositories; never the production remote."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

import autoscrape_autoupdate as updater
from custom_modules import updater_publication as publication_module
from custom_modules.git_workflow import (
    _git, GitTransportError, GitWorkflowError, require_clean_current_master, runtime_directory,
)
from custom_modules.narou_database import connect, initialize, finalize
from custom_modules.updater_publication import Publication
from custom_modules.updater_safety import FatalUpdaterError, atomic_json
from custom_modules.webscraper import TransportConfig


@pytest.fixture
def writer_repo(tmp_path):
    remote = tmp_path / 'remote.git'
    repository = tmp_path / 'writer'
    remote.mkdir()
    _git(remote, 'init', '--bare', '--initial-branch=master')
    _git(tmp_path, 'clone', str(remote), str(repository))
    _git(repository, 'config', 'user.name', 'Updater test')
    _git(repository, 'config', 'user.email', 'updater@example.invalid')
    _git(repository, 'config', 'core.autocrlf', 'false')
    _git(repository, 'config', 'commit.gpgsign', 'false')
    (repository / 'datastores').mkdir()
    connection = connect(repository / 'datastores' / 'narou.sqlite', writable=True)
    initialize(connection)
    finalize(connection)
    connection.close()
    (repository / 'README.md').write_text('Unrelated tracked file\n', encoding='utf-8')
    _git(repository, 'add', '.')
    _git(repository, 'commit', '-m', 'Initial')
    _git(repository, 'push', '-u', 'origin', 'master')
    return repository, remote


def candidate(repository):
    item = Publication(repository, runtime_directory(repository))
    item.prepare()
    (item.stage / 'chapters').mkdir()
    (item.stage / 'chapters' / 'one.json').write_text('{"chapter": "one"}\n', encoding='utf-8')
    (item.stage / 'index_scrape_results.json').write_text('[]\n', encoding='utf-8')
    return item, item.record(_git(repository, 'rev-parse', 'HEAD'), ['00001'])


def assert_published(repository, remote, base):
    assert _git(repository, 'rev-parse', 'HEAD') == _git(remote, 'rev-parse', 'master')
    assert _git(repository, 'rev-list', '--count', f'{base}..HEAD') == '1'
    assert not _git(repository, 'status', '--porcelain')
    assert (repository / 'README.md').read_text(encoding='utf-8') == 'Unrelated tracked file\n'


@pytest.mark.parametrize('boundary', ['before-promote', 'after-one-file', 'after-promotion',
                                     'before-commit', 'after-commit', 'before-push', 'after-push'])
def test_restart_at_every_publication_boundary(writer_repo, boundary):
    repository, remote = writer_repo
    item, record = candidate(repository)
    original_git = publication_module._git
    original_write = publication_module.atomic_copy
    tripped = False

    def fail_git(repo, *args, **kwargs):
        nonlocal tripped
        if not tripped and ((boundary == 'before-commit' and args[0] == 'commit')
                            or (boundary == 'before-push' and args[0] == 'push')):
            tripped = True
            raise GitTransportError('injected interruption')
        result = original_git(repo, *args, **kwargs)
        if not tripped and ((boundary == 'after-commit' and args[0] == 'commit')
                            or (boundary == 'after-push' and args[0] == 'push')):
            tripped = True
            raise GitTransportError('injected lost acknowledgment')
        return result

    def fail_write(source, target, **kwargs):
        nonlocal tripped
        if boundary == 'before-promote' and not tripped:
            tripped = True
            raise OSError('injected interruption')
        original_write(source, target, **kwargs)
        if boundary == 'after-one-file' and not tripped:
            tripped = True
            raise OSError('injected interruption')

    def phase(value):
        nonlocal tripped
        if boundary == 'after-promotion' and value == 'committing':
            tripped = True
            raise OSError('injected interruption')

    with patch.object(publication_module, '_git', side_effect=fail_git), \
            patch.object(publication_module, 'atomic_copy', side_effect=fail_write):
        with pytest.raises((OSError, GitTransportError)):
            item.finish(record, phase=phase)
    assert tripped and item.journal.exists()
    # Reconstruct exactly as a fresh process would; no in-memory record survives.
    restarted = Publication(repository, runtime_directory(repository))
    assert restarted.finish() == 1
    assert not restarted.journal.exists()
    assert_published(repository, remote, record['base'])


@pytest.mark.parametrize('damage', ['unrelated-file', 'owned-file', 'candidate', 'journal', 'branch', 'unowned-commit', 'index-only'])
def test_recovery_rejects_unowned_or_corrupt_state(writer_repo, damage):
    repository, _ = writer_repo
    item, record = candidate(repository)
    if damage == 'unrelated-file':
        (repository / 'README.md').write_text('User change', encoding='utf-8')
    elif damage == 'owned-file':
        (repository / 'datastores' / 'index_scrape_results.json').write_text('User change', encoding='utf-8')
    elif damage == 'candidate':
        (item.stage / 'index_scrape_results.json').write_text('Damaged', encoding='utf-8')
    elif damage == 'journal':
        record['files']['../README.md'] = next(iter(record['files'].values()))
        atomic_json(item.journal, record)
    elif damage == 'branch':
        _git(repository, 'checkout', '-b', 'other')
    elif damage == 'index-only':
        path = repository / 'datastores' / 'index_scrape_results.json'
        path.write_text('Unowned staged bytes', encoding='utf-8')
        _git(repository, 'add', '--', str(path))
        path.unlink()
    else:
        _git(repository, 'commit', '--allow-empty', '-m', 'User commit')
    before = {p.relative_to(repository): p.read_bytes() for p in repository.rglob('*')
              if p.is_file() and '.git' not in p.relative_to(repository).parts}
    with pytest.raises(FatalUpdaterError):
        item.finish()
    assert item.journal.exists()
    assert before == {p.relative_to(repository): p.read_bytes() for p in repository.rglob('*')
                      if p.is_file() and '.git' not in p.relative_to(repository).parts}


@pytest.mark.parametrize('boundary', ['file-during', 'file', 'commit', 'push'])
def test_abrupt_process_exit_recovers_durable_journal(writer_repo, boundary):
    repository, remote = writer_repo
    item, record = candidate(repository)
    code = '''
import os, sys
from pathlib import Path
from custom_modules import updater_publication as module
from custom_modules.git_workflow import runtime_directory
from custom_modules.updater_safety import ProcessLock
repository = Path(sys.argv[1])
boundary = sys.argv[2]
original_git, original_write = module._git, module.atomic_copy
def git(repo, *args, **kwargs):
    result = original_git(repo, *args, **kwargs)
    if args[0] == boundary:
        os._exit(77)
    return result
def write(path, data, **kwargs):
    if boundary == 'file-during':
        kwargs['temporary'].parent.mkdir(parents=True, exist_ok=True)
        kwargs['temporary'].write_bytes(b'partial interrupted copy')
        os._exit(77)
    original_write(path, data, **kwargs)
    if boundary == 'file':
        os._exit(77)
module._git, module.atomic_copy = git, write
state = runtime_directory(repository)
with ProcessLock(state / 'writer.lock'):
    module.Publication(repository, state).finish()
'''
    import sys
    process = subprocess.run([sys.executable, '-c', code, str(repository), boundary], timeout=10)
    assert process.returncode == 77
    assert Publication(repository, runtime_directory(repository)).finish() == 1
    assert_published(repository, remote, record['base'])


def advance_remote(repository, remote, tmp_path):
    other = tmp_path / 'other'
    _git(tmp_path, 'clone', str(remote), str(other))
    _git(other, 'config', 'user.name', 'Other writer')
    _git(other, 'config', 'user.email', 'other@example.invalid')
    _git(other, 'config', 'commit.gpgsign', 'false')
    (other / 'extra.txt').write_text('Remote advancement', encoding='utf-8')
    _git(other, 'add', '.')
    _git(other, 'commit', '-m', 'Remote update')
    _git(other, 'push')
    return other


def test_remote_divergence_preserves_pending_publication(writer_repo, tmp_path):
    repository, remote = writer_repo
    item, record = candidate(repository)
    advance_remote(repository, remote, tmp_path)
    with pytest.raises(FatalUpdaterError, match='diverged'):
        item.finish(record)
    assert item.journal.exists()
    assert _git(repository, 'rev-parse', 'HEAD') != _git(remote, 'rev-parse', 'master')


def test_clean_checkout_fast_forwards_but_unowned_ahead_commit_does_not(writer_repo, tmp_path):
    repository, remote = writer_repo
    advance_remote(repository, remote, tmp_path)
    require_clean_current_master(repository)
    assert _git(repository, 'rev-parse', 'HEAD') == _git(remote, 'rev-parse', 'master')
    _git(repository, 'commit', '--allow-empty', '-m', 'Unowned ahead commit')
    with pytest.raises(GitWorkflowError, match='ahead of or diverged'):
        require_clean_current_master(repository)


def test_real_update_discovery_noop_and_append(writer_repo, mock_service):
    repository, remote = writer_repo
    transport = TransportConfig(mock_origin=mock_service.origin, max_tries=1, retry_max_seconds=3)
    assert asyncio.run(updater.update_once(repository, transport=transport)) == 3
    before = {p.relative_to(repository): p.read_bytes() for p in (repository / 'datastores').rglob('*') if p.is_file()}
    head = _git(repository, 'rev-parse', 'HEAD')
    assert asyncio.run(updater.update_once(repository, transport=transport)) == 0
    assert _git(repository, 'rev-parse', 'HEAD') == head
    assert before == {p.relative_to(repository): p.read_bytes() for p in (repository / 'datastores').rglob('*') if p.is_file()}
    mock_service.state('pagination-boundary')
    assert asyncio.run(updater.update_once(repository, transport=transport)) == 2
    assert not _git(repository, 'status', '--porcelain')
    assert _git(repository, 'rev-parse', 'HEAD') == _git(remote, 'rev-parse', 'master')


def test_recovery_precedes_new_scrape(writer_repo):
    repository, _ = writer_repo
    candidate(repository)
    with patch.object(updater, 'scrape_and_persist', side_effect=AssertionError('must recover first')):
        assert asyncio.run(updater.update_once(repository)) == 1


@pytest.mark.skipif(not shutil.which('git-lfs'), reason='Git LFS not installed locally; exercised in CI')
@pytest.mark.parametrize('interruption', [None, 'commit'])
def test_sqlite_lfs_publication(writer_repo, interruption):
    repository, remote = writer_repo
    (repository / '.gitattributes').write_text('datastores/narou.sqlite filter=lfs diff=lfs merge=lfs -text\n', encoding='utf-8')
    _git(repository, 'lfs', 'install', '--local')
    _git(repository, 'add', '--renormalize', 'datastores/narou.sqlite')
    _git(repository, 'add', '.gitattributes')
    _git(repository, 'commit', '-m', 'Track SQLite using LFS')
    _git(repository, 'push')
    item = Publication(repository, runtime_directory(repository))
    item.prepare()
    connection = connect(item.stage / 'narou.sqlite', writable=True)
    connection.execute('CREATE TABLE test_lfs (value TEXT)')
    finalize(connection)
    connection.close()
    record = item.record(_git(repository, 'rev-parse', 'HEAD'), ['00001'])
    if interruption:
        original_git = publication_module._git

        def fail_commit(repo, *args, **kwargs):
            if args[0] == 'commit':
                raise GitTransportError('injected commit timeout')
            return original_git(repo, *args, **kwargs)

        with patch.object(publication_module, '_git', side_effect=fail_commit):
            with pytest.raises(GitTransportError):
                item.finish(record)
        assert item.finish() == 1
    else:
        assert item.finish(record) == 1
    assert _git(repository, 'show', 'HEAD:datastores/narou.sqlite').startswith('version https://git-lfs.github.com/spec/v1')
    assert_published(repository, remote, record['base'])
