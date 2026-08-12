#!/usr/bin/env bash
#
# Daily A/B: run the challenger model on the same prompt the published brief was
# built from. Publishes nothing — output lands in brief-bake-off/ for side-by-side
# reading. See brief_model_ab.py for why this is paired rather than alternating.
#
# Cron (daily 6:05 AM MST, after run_brief.sh writes the canonical brief):
#   5 6 * * * ~/claude-code-projects/tucson-daily-brief-site/run_brief_ab.sh >> /tmp/brief-ab.log 2>&1

set -uo pipefail
set -a
for conf in "$HOME/.config/environment.d/"*.conf; do
    [ -f "$conf" ] && source "$conf"
done
set +a

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== Model A/B run: $(date) ==="
# DAILY BAKE-OFF (standing experiment, started 2026-07-30 at the user's
# request; runs until they say stop).
#
#   Opus 5  -> the real brief, via run_brief.sh + the full production
#             pipeline. Untouched by this script.
#   Sol     -> challenger, files only
#   Terra   -> challenger, files only
#   Flash   -> challenger, files only (deepseek-v4-flash, added 2026-07-31)
#   Grok    -> challenger, files only (grok-4.6, added 2026-08-12 launch day)
#
# --no-telegram is deliberate: the point is to read real briefs side by
# side in brief-bake-off/, and three briefs every morning would get the alert
# channel muted. Luna was dropped 2026-07-30 (buried a live Extreme Heat
# Warning last on the morning it took effect).
#
# Cost ~$0.40/day (~$12/mo) — Flash adds under a cent, Grok ~$0.08 (est.
# 2026-08-12, remeasure from ab.jsonl after a few runs). Named arms are
# explicit on purpose: leaving this bare would run every challenger and
# multiply that. NOTE the script exits before ANY arm runs if a named arm's
# key is missing, so don't add an arm here until its key is in
# ~/.config/environment.d/ (cron does not read ~/.bashrc).
"$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/brief_model_ab.py" \
    --models sol,terra,flash,grok --no-telegram
echo "=== Done: $(date) ==="
