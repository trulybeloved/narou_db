import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap_narou_sqlite as bootstrap
from autoscrape_autoupdate import find_mismatches
from custom_modules import git_workflow
from custom_modules.narou_database import atomic_update, connect, get_index, initialize, upsert_chapter, validate


def index(uid=1, title='Title'):
    return {
        'chapter_uid': uid, 'chapter_title': title,
        'narou_link': f'https://ncode.syosetu.com/n2267be/{uid}/',
        'publication_timestamp': 1, 'chapter_edited': 0,
        'edit_timestamp': None, 'scraped_timestamp': 2,
    }


def chapter(title='Title', body='Body'):
    return {
        'chapter_title': title, 'chapter_body': body, 'chapter_id': 'rza1c1',
        'chapter_type': 'MAIN', 'arc_number': '1', 'chapter_number': '1',
        'jp_chapter_name': title, 'scraped_timestamp': 2,
    }


class NarouDatabaseTests(unittest.TestCase):
    def test_git_writer_rejects_a_dirty_checkout(self):
        with patch.object(git_workflow, '_git', return_value=' M datastores/narou.sqlite'):
            with self.assertRaisesRegex(git_workflow.GitWorkflowError, 'not clean'):
                git_workflow.require_clean_current_master(Path('.'))

    def test_push_failure_is_propagated_without_rollback(self):
        def fake_git(_repository, *args):
            if args[:2] == ('status', '--porcelain'):
                return ' M datastores/narou.sqlite'
            if args[0] == 'push':
                raise git_workflow.GitWorkflowError('push failed')
            return ''

        with patch.object(git_workflow, '_git', side_effect=fake_git):
            with self.assertRaisesRegex(git_workflow.GitWorkflowError, 'push failed'):
                git_workflow.commit_datastores_and_push(Path('.'), 'update')

    def test_index_comparison_only_selects_new_or_changed_records(self):
        stored = [index(), index(uid=2, title='Old')]
        local = [index(), index(uid=2, title='New'), index(uid=3)]
        self.assertEqual([2, 3], [row['chapter_uid'] for row in find_mismatches(local, stored)])

    def test_upsert_replaces_body_and_returns_index(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'narou.sqlite'
            connection = connect(database, writable=True)
            initialize(connection)
            upsert_chapter(connection, index(), chapter(body='first'))
            upsert_chapter(connection, index(title='Changed'), chapter(title='Changed', body='second'))
            connection.commit()
            validate(connection, expected_uids=[1])
            row = connection.execute('SELECT chapter_title, chapter_body FROM chapters').fetchone()
            self.assertEqual(('Changed', 'second'), tuple(row))
            connection.close()
            self.assertEqual('Changed', get_index(database)[0]['chapter_title'])

    def test_atomic_update_rolls_back_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'narou.sqlite'
            connection = connect(database, writable=True)
            initialize(connection)
            upsert_chapter(connection, index(), chapter())
            connection.commit()
            connection.close()

            def failing_update(target):
                upsert_chapter(target, index(title='Changed'), chapter(title='Changed'))
                raise RuntimeError('stop')

            with self.assertRaisesRegex(RuntimeError, 'stop'):
                atomic_update(database, failing_update)
            self.assertEqual('Title', get_index(database)[0]['chapter_title'])

    def test_bootstrap_reads_legacy_current_and_raw_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chapter_dir, raw_dir = root / 'chapters', root / 'raw'
            chapter_dir.mkdir()
            raw_dir.mkdir()
            (chapter_dir / 'legacy.json').write_text(json.dumps({
                'scraped_url': 'https://ncode.syosetu.com/n2267be/1/',
                'scrape_results': {'.novel_subtitle': '<p>Legacy</p>', '#novel_honbun': '<div><p>Old body</p></div>'},
            }), encoding='utf-8')
            (chapter_dir / 'current.json').write_text(json.dumps({
                'scraped_url': 'https://ncode.syosetu.com/n2267be/2/',
                'scrape_results': {'.p-novel__title': '<p>Current</p>', '.p-novel__body': '<div><p>New body</p></div>'},
            }), encoding='utf-8')
            (raw_dir / '00003.txt').write_text('Fallback title\n\nFallback body', encoding='utf-8')
            database = root / 'narou.sqlite'
            index_file = root / 'index.json'
            index_file.write_text('[]', encoding='utf-8')
            with patch.object(bootstrap, 'CHAPTERS_DIR', chapter_dir), patch.object(bootstrap, 'RAW_TEXT_DIR', raw_dir), patch.object(bootstrap, 'INDEX_SCRAPE_FILE', index_file):
                self.assertEqual(0, bootstrap.build(database))
            self.assertEqual([1, 2, 3], [row['chapter_uid'] for row in get_index(database)])
            connection = connect(database)
            try:
                validate(connection, expected_uids=[1, 2, 3])
            finally:
                connection.close()

    def test_validate_detects_corrupt_content_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'narou.sqlite'
            connection = connect(database, writable=True)
            initialize(connection)
            upsert_chapter(connection, index(), chapter())
            connection.execute("UPDATE chapters SET content_sha256 = 'wrong'")
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validate(connection)
            connection.close()
