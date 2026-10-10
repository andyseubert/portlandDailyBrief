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
        if not settings['window_hours'] <= settings['late_arrival_lookback_hours'] <= 168:
            raise ValueError('Late arrival lookback must cover the publication window and be at most seven days')
    ZoneInfo(settings["timezone"])
    if settings.get("public_publishing") or settings.get("scheduling"):
        raise ValueError("This local build does not support automatic publishing or scheduling")
    for name in ("story_limit", "per_source_limit", "max_tokens_per_run", "model_attempts"):
        if not isinstance(settings[name], int) or settings[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not 0 < settings["window_hours"] <= 168:
        raise ValueError("window_hours must be between 0 and 168")
    if len({s["id"] for s in sources}) != len(sources):
        raise ValueError("Source IDs must be unique")
    return settings, sources
