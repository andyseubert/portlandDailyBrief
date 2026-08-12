#!/usr/bin/env python3
"""TDB Weekly newsletter model bake-off.

Can a different model write a better (or equally good, cheaper) TDB Weekly than
the production Sonnet 4.6? Same question and same method as brief_model_ab.py:
run challengers on the BYTE-IDENTICAL prompt the published draft was built
from, then read the drafts side by side. The prompt is captured by
monkeypatching generate_newsletter.call_claude — deliberately not a
re-implementation of the assembly, so it can never drift from production.
(The generator printing "ERROR: failed to generate draft" during capture is
the monkeypatch working as designed.)

WHY THIS ONE IS WEEKLY AND INTERACTIVE
The newsletter ships once a week, on a Saturday, with a human already reading
the draft. So this runs as part of the Saturday ritual (run_newsletter.sh
invokes it after the champion draft is generated), n grows by 1 per week, and
the comparison is read on the spot — no Telegram summary, no cron.

WHAT THE COMPARISON IS ABOUT
The newsletter is a VOICE product ("warm Tucson neighbor on a Sunday
morning"), not a completeness product like the brief. Read for: does it hold
the voice without slipping into civic-tech phrasing, does it hedge business
openings correctly (the prompt's hardest rule), does it fabricate. Cost is
logged because it is free to capture, but at ~4 runs/month even Sol's price
is pocket change — prose quality decides this one.

WHAT IT DOES NOT DO
Publishes nothing, uploads nothing to Buttondown, writes only to
newsletter-bake-off/ (gitignored).

Usage:
    python3 newsletter_model_ab.py                    # all arms
    python3 newsletter_model_ab.py --models sonnet5
    python3 newsletter_model_ab.py --send-date 2026-08-09
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE))
OUT_DIR = SITE / "newsletter-bake-off"
DRAFTS_DIR = SITE / "newsletter" / "drafts"

# The champion's own max_tokens is 4000, but Sonnet 5 runs adaptive thinking by
# default and GPT-5.6 reasons by default, and both bill thinking against the
# output cap — 4000 would risk finish=length with an empty draft (the exact
# failure the brief bake-off hit with DeepSeek). Headroom is nearly free; the
# drafts themselves stay ~1200 words because the prompt says so.
MAX_TOKENS = 16000

PROVIDERS = {
    "openai": dict(url="https://api.openai.com/v1/chat/completions",
                   env="OPENAI_API_KEY", token_param="max_completion_tokens"),
    "anthropic": dict(url="https://api.anthropic.com/v1/messages",
                      env="ANTHROPIC_API_KEY", token_param="max_tokens"),
    # reasoning_extra: xAI reports reasoning tokens disjoint from
    # completion_tokens and bills them as output — see brief_model_ab.py.
    "xai": dict(url="https://api.x.ai/v1/chat/completions",
                env="XAI_API_KEY", token_param="max_tokens",
                reasoning_extra=True),
}

# $/MTok (input, output). OpenAI rates re-verified 2026-08-08 (unchanged since
# the 2026-07-30 cut: Sol 5/30, Terra 2/12; short-context rates — the
# newsletter prompt is well inside short context). Sonnet 5 launched at 3/15
# with an INTRO price of 2/10 through 2026-08-31 — priced by run date below,
# so early ab.jsonl rows carry the intro price; re-read them knowing that.
SONNET5_INTRO_UNTIL = date(2026, 8, 31)
SONNET5_PRICE = ((2.00, 10.00) if date.today() <= SONNET5_INTRO_UNTIL
                 else (3.00, 15.00))

CHALLENGERS = {
    "sonnet5": dict(provider="anthropic", model="claude-sonnet-5",
                    price=SONNET5_PRICE),
    "sol":     dict(provider="openai", model="gpt-5.6-sol",   price=(5.00, 30.00)),
    "terra":   dict(provider="openai", model="gpt-5.6-terra", price=(2.00, 12.00)),
    # Grok 4.6, added 2026-08-12 (launch day) — same entry as brief_model_ab.py:
    # $2/$6 per docs.x.ai, reasoning bills as output, headroom over the default
    # output budget so thinking can't starve the draft.
    "grok":    dict(provider="xai", model="grok-4.6",
                    price=(2.00, 6.00), max_tokens=48000),
}
# Champion = the draft actually generated for this send (Sonnet 4.6, 3/15).
# Not re-run — copied in for side-by-side reading.


def next_sunday(today: date) -> date:
    days_ahead = (6 - today.weekday()) % 7
    return today if days_ahead == 0 else today + timedelta(days=days_ahead)


def capture_prompt(send_date: str):
    import generate_newsletter as gn
    captured = {}

    def spy(prompt, api_key):
        captured["prompt"] = prompt
        return None

    real, gn.call_claude = gn.call_claude, spy
    # --force so an existing champion draft doesn't abort the capture run;
    # main() exits 1 when the spy returns None — that is expected.
    argv, sys.argv = sys.argv, ["generate_newsletter.py", "--force",
                                "--send-date", send_date]
    try:
        gn.main()
    except SystemExit:
        pass
    finally:
        gn.call_claude, sys.argv = real, argv
    if not captured.get("prompt"):
        raise RuntimeError(
            "could not capture the newsletter prompt — most likely no puzzle "
            "is locked for the send date (the generator hard-stops before the "
            "API call). Generate the Tucson Mini first.")
    return captured["prompt"]


def _post(url, body, headers, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run_chat(spec, prompt, keys):
    p = PROVIDERS[spec["provider"]]
    data = _post(p["url"], {
        "model": spec["model"],
        # Per-spec override for reasoning models whose thinking bills against
        # the output budget (grok arm) — same convention as brief_model_ab.py.
        p["token_param"]: spec.get("max_tokens", MAX_TOKENS),
        "messages": [{"role": "user", "content": prompt}],
    }, {"Content-Type": "application/json",
        "Authorization": f"Bearer {keys[spec['provider']]}"})
    choice = (data.get("choices") or [{}])[0]
    u = data.get("usage") or {}
    out = u.get("completion_tokens")
    thinking = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    if p.get("reasoning_extra") and out is not None:
        out += thinking  # billed as output but reported disjoint (see PROVIDERS)
    return (choice.get("message") or {}).get("content"), {
        "in": u.get("prompt_tokens"),
        "out": out,
        "thinking": thinking,
        "finish": choice.get("finish_reason"),
    }


def run_anthropic(spec, prompt, keys):
    data = _post(PROVIDERS["anthropic"]["url"], {
        "model": spec["model"],
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }, {"Content-Type": "application/json",
        "x-api-key": keys["anthropic"],
        "anthropic-version": "2023-06-01"})
    blocks = data.get("content") or []
    text = next((b.get("text") for b in blocks if b.get("type") == "text"), None)
    u = data.get("usage") or {}
    return text, {
        "in": u.get("input_tokens"),
        "out": u.get("output_tokens"),
        "thinking": 0,  # thinking is inside output_tokens; not broken out
        "finish": data.get("stop_reason"),
    }


RUNNERS = {"openai": run_chat, "xai": run_chat, "anthropic": run_anthropic}


def run_one(spec, prompt, keys):
    """One challenger. Never raises — a dead arm must not kill the others."""
    t0 = time.time()
    try:
        text, meta = RUNNERS[spec["provider"]](spec, prompt, keys)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return None, {"error": f"HTTP {e.code}: {detail}", "secs": round(time.time()-t0, 1)}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}", "secs": round(time.time()-t0, 1)}

    pin, pout = spec["price"]
    meta["secs"] = round(time.time() - t0, 1)
    meta["usd"] = round(((meta.get("in") or 0) * pin
                         + (meta.get("out") or 0) * pout) / 1e6, 4)
    if not text:
        meta["error"] = f"empty response (finish={meta.get('finish')}, " \
                        f"{meta.get('thinking') or 0} thinking tokens)"
    return text, meta


def gate(text, prompt):
    """Provenance gate in shadow mode, reused from the brief pipeline. The
    newsletter prompt embeds all source content, so grounding checks work the
    same way. Never writes to the gate's own log."""
    try:
        import provenance_gate as pg
        return pg.check(text, prompt, mode=pg.Mode.SHADOW).summary()
    except Exception as e:
        return f"unavailable: {e}"


def voice_flags(text):
    """Cheap deterministic voice check: count occurrences of the prompt's own
    banned backstage phrases. Not a judgment — a pointer at lines to read."""
    banned = ["public records", "agenda mining", "local intelligence",
              "monitoring the situation", "our review", "our pipeline",
              "flagged by", "surfaced from", "came through our review",
              "according to filings", "per the data",
              "based on the agenda materials"]
    low = text.lower()
    hits = [p for p in banned if p in low]
    return ", ".join(hits) if hits else "clean"


def champion_draft(send_date):
    p = DRAFTS_DIR / f"tdb-weekly-{send_date}.md"
    if not p.exists():
        return None
    text = p.read_text()
    # Strip the generator's metadata header (everything through the first ---).
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].lstrip()
    return text


def write_comparison(send_date, rows, champ):
    lines = [f"# Newsletter bake-off — send {send_date}", "",
             "Byte-identical prompt; only the model varies. "
             "Champion = the Sonnet 4.6 draft actually generated for this send.", "",
             "| arm | model | words | secs | $ | voice flags | gate |",
             "|---|---|---|---|---|---|---|"]
    if champ is not None:
        lines.append(f"| **champion** | claude-sonnet-4-6 | {len(champ.split())} "
                     f"| — | — | {voice_flags(champ)} | — |")
    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['arm']} | {r['model']} | — | {r.get('secs','—')} "
                         f"| — | — | ERROR: {r['error'][:60]} |")
        else:
            lines.append(f"| {r['arm']} | {r['model']} | {r.get('words')} "
                         f"| {r.get('secs')} | {r.get('usd')} "
                         f"| {r.get('voice','—')} | {r.get('gate','n/a')} |")
    lines += ["", "## How to read this", "",
              "This is a voice product — the metrics only point at what to read. "
              "Judge each draft on: (1) does it hold the warm-neighbor voice, "
              "(2) does it hedge business openings per the prompt's rules, "
              "(3) did it invent anything (an UNGROUNDED gate finding is the "
              "strongest signal), (4) which one would you actually send. "
              "Voice flags = the prompt's own banned backstage phrases found "
              "verbatim; a hit is a pointer, not a verdict.",
              "", "Files:", ""]
    if champ is not None:
        lines.append(f"- `{send_date}-champion-claude-sonnet-4-6.md`")
    for r in rows:
        if not r.get("error"):
            lines.append(f"- `{send_date}-{r['model']}.md`")
    (OUT_DIR / f"{send_date}-comparison.md").write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help=f"comma-separated subset of {','.join(CHALLENGERS)}")
    ap.add_argument("--send-date", help="YYYY-MM-DD (default: next Sunday, same as the generator)")
    a = ap.parse_args()

    arms = [m.strip() for m in a.models.split(",")] if a.models else list(CHALLENGERS)
    unknown = [m for m in arms if m not in CHALLENGERS]
    if unknown:
        sys.exit(f"ERROR: unknown arm(s) {unknown}; known: {list(CHALLENGERS)}")

    send_date = a.send_date or next_sunday(date.today()).isoformat()

    keys = {p: os.environ.get(cfg["env"]) for p, cfg in PROVIDERS.items()}
    for provider in {CHALLENGERS[m]["provider"] for m in arms}:
        if not keys[provider]:
            sys.exit(f"ERROR: {PROVIDERS[provider]['env']} not set.")

    OUT_DIR.mkdir(exist_ok=True)
    prompt = capture_prompt(send_date)
    print(f"\nprompt captured: {len(prompt):,} chars; arms: {', '.join(arms)}\n",
          file=sys.stderr)

    champ = champion_draft(send_date)
    if champ:
        (OUT_DIR / f"{send_date}-champion-claude-sonnet-4-6.md").write_text(champ)
        print(f"  champion copied in ({len(champ.split())} words)", file=sys.stderr)
    else:
        print("  WARN: no champion draft found for this send date", file=sys.stderr)

    rows = []
    for arm in arms:
        spec = CHALLENGERS[arm]
        print(f"  running {arm} ({spec['model']}) …", file=sys.stderr)
        text, meta = run_one(spec, prompt, keys)
        meta.update(arm=arm, model=spec["model"], send_date=send_date,
                    run_date=datetime.now().strftime("%Y-%m-%d"),
                    prompt_chars=len(prompt),
                    price_per_mtok=spec["price"])
        if text:
            (OUT_DIR / f"{send_date}-{spec['model']}.md").write_text(text)
            meta["words"] = len(text.split())
            meta["voice"] = voice_flags(text)
            meta["gate"] = gate(text, prompt)
            print(f"     {meta['words']}w  {meta['secs']}s  ${meta['usd']}  "
                  f"voice: {meta['voice']}  gate: {meta['gate']}", file=sys.stderr)
        else:
            print(f"     FAILED: {meta.get('error')}", file=sys.stderr)
        rows.append(meta)

    with open(OUT_DIR / "ab.jsonl", "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    write_comparison(send_date, rows, champ)

    ok = [r for r in rows if not r.get("error")]
    total = sum(r.get("usd") or 0 for r in ok)
    print(f"\n{len(ok)}/{len(rows)} arms succeeded; run cost ${total:.4f}",
          file=sys.stderr)
    print(f"Read side by side: {OUT_DIR / f'{send_date}-comparison.md'}",
          file=sys.stderr)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
