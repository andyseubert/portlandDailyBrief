"""Configuration is local; fetched text can never change settings."""
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def load_config(root: Path):
    settings = json.loads((root / "config/settings.json").read_text())
    sources = json.loads((root / "config/sources.json").read_text())
    task_path = root / 'config/tasks.json'
    if task_path.exists():
        tasks = json.loads(task_path.read_text())
        daily = tasks['daily_brief']
        if daily.get('provider'):
            settings['provider'] = daily['provider']
        if daily.get('model'):
            if daily['model'] != settings['model'] and not daily.get('model_revision'):
                raise ValueError('A task-specific model needs an explicit pinned revision')
            settings['model'] = daily['model']
            settings['model_revision'] = daily.get('model_revision', settings['model_revision'])
        settings['story_limit'] = min(settings['story_limit'], daily['story_maximum'])
        settings['late_arrival_lookback_hours'] = daily['late_arrival_lookback_hours']
        settings['scheduling'] = bool(tasks.get('schedule', {}).get('enabled'))
        settings['government_watch'] = tasks.get('government_watch', {})
        ai = tasks.get('ai_review',{})
        if ai.get('enabled'):
            settings['ai_review'] = {**ai,'model':settings['model'],'model_revision':settings['model_revision']}
        priorities = daily.get('editorial_priorities', [])
        for priority in priorities:
            if (not isinstance(priority.get('id'), str) or
                not isinstance(priority.get('terms'), list) or not priority['terms'] or
                any(not isinstance(t, str) or not t.strip() for t in priority['terms']) or
                type(priority.get('weight')) is not int or not 0 <= priority['weight'] <= 10):
                raise ValueError('Editorial priorities require an ID, literal search terms and a weight from 0 to 10')
        settings['editorial_priorities'] = priorities
        if not settings['window_hours'] <= settings['late_arrival_lookback_hours'] <= 168:
            raise ValueError('Late arrival lookback must cover the publication window and be at most seven days')
    ZoneInfo(settings["timezone"])
    if settings.get("public_publishing"):
        raise ValueError("Website publishing is not configured; repository drafts are a separate workflow")
    for name in ("story_limit", "per_source_limit", "max_tokens_per_run", "model_attempts"):
        if not isinstance(settings[name], int) or settings[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not 0 < settings["window_hours"] <= 168:
        raise ValueError("window_hours must be between 0 and 168")
    if len({s["id"] for s in sources}) != len(sources):
        raise ValueError("Source IDs must be unique")
    settings['official_local_source_ids'] = [s['id'] for s in sources if s.get('official_jurisdiction') in {'portland', 'multnomah'}]
    settings['source_balance_groups'] = {s['id']: s.get('source_group', s['id']) for s in sources}
    return settings, sources
