"""Public summary-only Git archive. Private evidence never enters the export.

Andy authorized daily repository review drafts on October 10, 2026. This is
separate from editorial approval, civic publication, and website deployment.
"""
import argparse
from datetime import date
from pathlib import Path
import json
import subprocess

from .config import load_config
from .pipeline import run_lock
from .records import Evidence, safe_url
from .rendering import atomic_text, html, markdown, page
from .storage import Store
from .validation import validate_story

ARCHIVE_PATHS = ['posts', 'briefings.html', 'briefings.md', 'index.html']


def git(root, *args):
    return subprocess.run(['/usr/bin/git', '-C', str(root), *args],
                          check=True, capture_output=True, text=True, timeout=90).stdout.strip()


def validate(doc, registry, civic_store=None):
    if date.fromisoformat(doc['date']).isoformat() != doc['date']:
        raise ValueError('Invalid archive date')
    if doc['status'] not in {'draft', 'partial', 'editorial_draft'}:
        raise ValueError('Failed or incomplete generation cannot enter the archive')
    sources = {s['id']: s for s in registry}
    for story in doc['stories']:
        if len(story['citation_ids']) != 1:
            raise ValueError('Summary requires its validated evidence ID')
        evidence = Evidence(**doc['evidence'][story['citation_ids'][0]])
        source = sources[evidence.source_id]
        safe_url(evidence.url, source['allowed_hosts'])
        validate_story({key: story[key] for key in ('headline', 'sentences', 'citation_ids', 'evidence_spans')}, evidence)
    for key in ['forecast_url', 'alert_url']:
        if doc['weather'].get(key):
            safe_url(doc['weather'][key], ['api.weather.gov'])
    if doc.get('development_watch'):
        if civic_store is None:
            raise ValueError('Development content requires its civic evidence and editorial audit')
        from .civic.development_reports import validate_snapshot
        for project in doc['development_watch']:
            validate_snapshot(civic_store, project, doc['generated_at'])


def export(root):
    settings, registry = load_config(root)
    posts = root / 'posts'
    posts.mkdir(exist_ok=True)
    # The same lock used by generation/editing gives a consistent current version.
    with run_lock(root / settings['data_path'] / 'run.lock'):
        store = Store(root / settings['data_path'] / 'editorial.sqlite3')
        from .civic.store import CivicStore
        civic = CivicStore(root / settings['data_path'] / 'civic.sqlite3')
        try:
            documents = []
            for edition in store.list_editions():
                if edition['state'] not in {'needs_review', 'approved'}:
                    continue
                _, doc = store.get(edition['date'])
                validate(doc, registry, civic)
                documents.append(doc)
            # Validate the whole batch before replacing any last-known-good files.
            for doc in documents:
                atomic_text(posts / (doc['date'] + '.md'), markdown(doc, repository=True))
                atomic_text(posts / (doc['date'] + '.html'), html(doc, repository=True))
            dates = sorted([p.stem for p in posts.glob('????-??-??.md')
                            if p.with_suffix('.html').exists()], reverse=True)
            if dates:
                latest = next((d for d in documents if d['date'] == dates[0]), None)
                if latest:
                    atomic_text(root / 'index.html', html(latest, root_index=True, repository=True))
                links = ''.join(f'<li><a href="posts/{day}.html">{day}</a> · <a href="posts/{day}.md">Markdown</a> — review draft</li>' for day in dates)
                atomic_text(root / 'briefings.html', page('Portland daily archive', '<h1>Portland Daily Brief archive</h1><p>AI-assisted review drafts. Editorial approval is separate. Forecasts and claims are dated snapshots.</p><ul>' + links + '</ul>', '<a href="index.html">Latest brief</a>'))
                atomic_text(root / 'briefings.md', '# Portland Daily Brief archive\n\nAI-assisted review drafts; editorial approval pending. Historical forecasts describe the time checked. Corrections and earlier versions remain in Git history.\n\n' + '\n'.join(f'- [{day}](posts/{day}.md) · [HTML](posts/{day}.html)' for day in dates) + '\n')
            return dates
        finally:
            civic.close()
            store.close()


def allowed(path):
    return path in ARCHIVE_PATHS or (path.startswith('posts/') and
           Path(path).suffix in {'.md', '.html'} and
           len(Path(path).parts) == 2 and date.fromisoformat(Path(path).stem).isoformat() == Path(path).stem)


def sync(root, push=False):
    tasks = json.loads((root / 'config/tasks.json').read_text())
    config = tasks.get('repository_archive', {})
    if not config.get('enabled'):
        raise ValueError('Repository archive is disabled')
    with run_lock(root / 'data/repository-archive.lock'):
        if push:
            if git(root, 'branch', '--show-current') != 'main':
                raise ValueError('Automatic archive sync requires the main branch')
            if git(root, 'diff', '--cached', '--name-only'):
                raise ValueError('Staged changes require manual handling; nothing pushed')
            changed = git(root, 'diff', '--name-only').splitlines()
            if any(not allowed(p) for p in changed):
                raise ValueError('Other tracked edits require manual handling; nothing pushed')
            if git(root, 'remote', 'get-url', 'origin') != config['remote_url']:
                raise ValueError('Origin does not match the configured repository')
            # Do not push unrelated local commits on the next unattended retry.
            pending = git(root, 'log', '--format=', '--name-only', 'origin/main..HEAD').splitlines()
            if any(p and not allowed(p) for p in pending):
                raise ValueError('Unrelated local commits require a manual push')
        dates = export(root)
        committed = False
        if push and dates:
            # Never stage a whole directory: unrelated untracked private files
            # could have been placed inside posts/ by a manual workflow.
            files = ARCHIVE_PATHS[1:] + [f'posts/{day}{suffix}' for day in dates for suffix in ['.md', '.html']]
            git(root, 'add', '--', *files)
            if git(root, 'diff', '--cached', '--name-only'):
                git(root, 'commit', '-m', 'Archive Portland daily brief ' + dates[0])
                committed = True
            # A failed push is retried even when no newly generated file changed.
            git(root, 'push', 'origin', 'HEAD:main')
        return {'dates': dates, 'committed': committed, 'pushed': bool(push and dates),
                'editorial_approval': 'unchanged', 'website_deployed': False}


def main(argv=None):
    p = argparse.ArgumentParser(description='Export summary-only dated repository review drafts')
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--push', action='store_true')
    args = p.parse_args(argv)
    try:
        print(json.dumps(sync(args.root.resolve(), args.push), indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print('Archive sync failed: ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
