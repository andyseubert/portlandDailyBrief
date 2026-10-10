from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from portland_brief.launch_agent import specification
from portland_brief.repository_archive import export, git, sync
from portland_brief.scheduling import initial_daily, main as schedule_main, tick
from portland_brief.storage import Store
from tests.test_brief import doc, ROOT


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        shutil.copytree(ROOT / 'config', self.root / 'config')
        self.store = Store(self.root / 'data/editorial.sqlite3')
        self.record = doc()
        sources = [{'id': 'portland', 'allowed_hosts': ['www.portland.gov']}]
        (self.root / 'config/sources.json').write_text(json.dumps(sources))
        self.save('original', self.record)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save(self, name, record):
        self.store.save_run(name, record)
        self.store.save_edition(name, record)

    def test_export_private_material_excluded_and_repeat_is_identical(self):
        export(self.root)
        first = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.glob('posts/*')}
        text = (self.root / 'posts/2026-10-09.md').read_text()
        self.assertIn('REVIEW DRAFT', text)
        self.assertIn('https://www.portland.gov/news/test', text)
        self.assertNotIn(self.record['evidence']['e_test']['text'], text)
        self.assertFalse(list((self.root / 'posts').glob('*.json')))
        self.assertEqual(self.store.get('2026-10-09')[0]['state'], 'needs_review')
        export(self.root)
        self.assertEqual(first, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.glob('posts/*')})
        self.assertEqual((self.root / 'briefings.md').read_text().count('- [2026-10-09]'), 1)

    def test_older_edition_cannot_replace_newest_and_bad_citation_preserves_files(self):
        newer = deepcopy(self.record); newer['date'] = '2026-10-10'
        self.save('newer', newer)
        export(self.root)
        homepage = (self.root / 'index.html').read_bytes()
        self.assertIn(b'2026-10-10', homepage)
        self.save('old-rerun', self.record)
        export(self.root)
        self.assertEqual(homepage, (self.root / 'index.html').read_bytes())
        bad = deepcopy(self.record); bad['stories'][0]['citation_ids'] = ['invented']
        self.save('bad', bad)
        with self.assertRaises(KeyError):
            export(self.root)
        self.assertEqual(homepage, (self.root / 'index.html').read_bytes())

    def initialize_git(self):
        bare = Path(self.temp.name) / 'remote.git'
        subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
        git(self.root, 'init', '-b', 'main')
        git(self.root, 'config', 'user.email', 'test@example.test')
        git(self.root, 'config', 'user.name', 'Test Editor')
        (self.root / '.gitignore').write_text('data/\n')
        config_path = self.root / 'config/tasks.json'
        config = json.loads(config_path.read_text())
        config['repository_archive'].update(enabled=True, remote_url=str(bare))
        config_path.write_text(json.dumps(config))
        git(self.root, 'add', '--', '.gitignore', 'config')
        git(self.root, 'commit', '-m', 'Setup')
        git(self.root, 'remote', 'add', 'origin', str(bare))
        git(self.root, 'push', '-u', 'origin', 'main')

    def test_git_history_retains_correction_and_unchanged_sync_no_commit(self):
        self.initialize_git()
        self.assertTrue(sync(self.root, True)['committed'])
        first = git(self.root, 'rev-parse', 'HEAD')
        self.assertFalse(sync(self.root, True)['committed'])
        self.assertEqual(first, git(self.root, 'rev-parse', 'HEAD'))
        changed = deepcopy(self.record)
        changed['stories'][0]['headline'] = 'Portland drainage improvements on Greeley Avenue'
        self.save('corrected', changed)
        self.assertTrue(sync(self.root, True)['committed'])
        prior = git(self.root, 'show', first + ':posts/2026-10-09.md')
        self.assertIn(self.record['stories'][0]['headline'], prior)

    def test_failed_push_retries_without_duplicate_commit(self):
        self.initialize_git()
        def offline(root, *args):
            if args[0] == 'push':
                raise subprocess.CalledProcessError(1, ['git', 'push'])
            return git(root, *args)
        with patch('portland_brief.repository_archive.git', side_effect=offline):
            with self.assertRaises(subprocess.CalledProcessError):
                sync(self.root, True)
        commit = git(self.root, 'rev-parse', 'HEAD')
        result = sync(self.root, True)
        self.assertTrue(result['pushed'])
        self.assertFalse(result['committed'])
        self.assertEqual(commit, git(self.root, 'rev-parse', 'origin/main'))

    def test_unrelated_staged_file_cannot_be_automatically_committed_or_pushed(self):
        self.initialize_git()
        (self.root / 'private.txt').write_text('private')
        git(self.root, 'add', '--', 'private.txt')
        with self.assertRaisesRegex(ValueError, 'Staged'):
            sync(self.root, True)
        self.assertFalse((self.root / 'posts').exists())

    def test_untracked_private_file_in_posts_not_staged(self):
        self.initialize_git()
        (self.root / 'posts').mkdir()
        (self.root / 'posts/private-evidence.json').write_text('private evidence')
        sync(self.root, True)
        self.assertNotIn('private-evidence.json', git(self.root, 'ls-tree', '-r', '--name-only', 'HEAD'))

    def test_manual_edition_satisfies_already_due_scheduled_occurrence(self):
        tick(self.root, False, datetime(2026,10,10,12,tzinfo=timezone.utc))
        newer = deepcopy(self.record); newer['date'] = '2026-10-10'
        self.save('manual-today', newer)
        def runner(command, **kwargs):
            self.assertNotEqual(command[-1], 'run')
            return subprocess.CompletedProcess(command, 0)
        result = tick(self.root, True, datetime(2026,10,10,18,tzinfo=timezone.utc), runner)
        self.assertNotIn('news-synthesis', [r['job'] for r in result['results']])

    def test_daily_catchup_after_wake_and_pause_switch(self):
        config = json.loads((self.root / 'config/tasks.json').read_text())
        after = datetime(2026,10,10,18,tzinfo=timezone.utc)
        self.assertEqual(initial_daily(self.root, after, config), after)
        self.assertEqual(initial_daily(self.root, datetime(2026,10,10,12,tzinfo=timezone.utc), config).isoformat(), '2026-10-10T14:00:00+00:00')
        newer = deepcopy(self.record); newer['date'] = '2026-10-10'
        self.save('today', newer)
        self.assertEqual(initial_daily(self.root, after, config).isoformat(), '2026-10-11T14:00:00+00:00')
        config['schedule']['enabled'] = False
        (self.root / 'config/tasks.json').write_text(json.dumps(config))
        with patch('portland_brief.scheduling.tick') as run:
            self.assertEqual(schedule_main(['--root',str(self.root),'tick','--run','--scheduled']), 0)
            run.assert_not_called()

    def test_launch_agent_uses_absolute_paths_and_rechecks_due_jobs(self):
        spec = plistlib.loads(plistlib.dumps(specification(self.root)))
        self.assertEqual(spec['StartInterval'], 60)
        self.assertIn('--scheduled', spec['ProgramArguments'])
        self.assertTrue(Path(spec['ProgramArguments'][0]).is_absolute())
        self.assertTrue(spec['RunAtLoad'])
