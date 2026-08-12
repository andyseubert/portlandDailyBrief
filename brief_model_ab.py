#!/usr/bin/env python3
"""brief_model_ab.py — run challenger models on the same prompt the published
brief was built from, so they can be read side by side.

THE QUESTION THIS ANSWERS
How far down the price ladder can we go and still get a publishable brief?
Capability is not the variable — GPT-5.6 Sol, Terra and Luna have identical
context windows, output limits, knowledge cutoffs, reasoning levels and tool
support; they differ only in price, by 25x end to end. A spec sheet cannot
tell you where quality stops holding. Reading five briefs about the same
morning's news can.

WHY PAIRED, NOT ALTERNATING
Alternating days measures the news, not the model: brief quality swings far more
with what happened yesterday than with which model wrote it. Running them all
over byte-identical input removes that variance entirely.

WHAT IT DOES NOT DO
Publishes nothing, writes nothing to the canonical brief path, and never touches
brief-inputs/shadow.jsonl (the provenance gate's own log). Challenger output and
metrics go to brief-bake-off/.

HOW THE PROMPT IS OBTAINED
generate_brief.main() is run with call_claude monkeypatched to capture its
argument and return None. main() then exits on "synthesis failed" and we keep
the prompt. This is deliberately not a re-implementation of the assembly: a
parallel copy would drift from production and quietly invalidate the comparison.
The "ERROR: synthesis failed" line in the log is that monkeypatch working as
designed, not a failure.

READ THE OUTPUT, NOT THE METRICS
Tokens, cost and latency are logged because they are free to capture, but they
are not the question. The question is which brief you would rather have
published — which stories each picked, which it missed, and whether either
asserted something the sources do not support. The provenance gate runs on every
arm and is the one automated quality signal here: an UNGROUNDED finding means
the model asserted a name the sources never contained. n=7 over a week has no
statistical power; this is a structured side-by-side, not a measurement.

COST
Running every arm is roughly $0.40/day (~$12/month) — several times the rest of
the pipeline combined, and almost all of it is sol/terra/opus. Use --models to
run a subset, or --cheap for the bottom of the ladder only. The DeepSeek arms
are rounding error: flash is ~$0.007/day, about 1/50th of an Opus 5 run.

Usage:
    python3 brief_model_ab.py                      # all challengers
    python3 brief_model_ab.py --models luna,terra  # a subset
    python3 brief_model_ab.py --cheap              # luna + terra only
    python3 brief_model_ab.py --no-telegram
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE))
OUT_DIR = SITE / "brief-bake-off"
SEND_TELEGRAM = Path.home() / ".openclaw/skills/tucson-daily-brief/scripts/send_telegram.py"
BRIEF_DIR = Path.home() / ".openclaw/workspace/briefings"

MAX_TOKENS = 16000

# Where each provider's API lives, which env var holds its key, and which
# max-output parameter it wants. GPT-5.x rejects `max_tokens` and requires
# `max_completion_tokens`; DeepSeek's OpenAI-compatible endpoint wants the
# original `max_tokens`. Same wire format otherwise, so both use run_chat().
PROVIDERS = {
    "openai": dict(url="https://api.openai.com/v1/chat/completions",
                   env="OPENAI_API_KEY", token_param="max_completion_tokens"),
    "anthropic": dict(url="https://api.anthropic.com/v1/messages",
                      env="ANTHROPIC_API_KEY", token_param="max_tokens"),
    "deepseek": dict(url="https://api.deepseek.com/v1/chat/completions",
                     env="DEEPSEEK_API_KEY", token_param="max_tokens"),
    # reasoning_extra: xAI reports usage.completion_tokens_details.reasoning_tokens
    # DISJOINT from completion_tokens (measured 2026-08-12: 11,331 reasoning vs
    # 2,221 completion on the first grok-4.6 run) and bills reasoning as output.
    # OpenAI and DeepSeek report reasoning as a SUBSET of completion_tokens, so
    # only xAI gets the flag — without it the grok cost undercounts ~2x.
    "xai": dict(url="https://api.x.ai/v1/chat/completions",
                env="XAI_API_KEY", token_param="max_tokens",
                reasoning_extra=True),
}

# $/MTok (input, output). VERIFIED 2026-07-30 against
# developers.openai.com/api/docs/pricing after OpenAI's price cut that day:
# Terra -20% (2.50/15 -> 2.00/12), Luna -80% (-> 0.20/1.20), Sol unchanged.
# These are SHORT-CONTEXT rates; OpenAI prices long context at roughly double
# but does not publish the boundary. The brief prompt is ~18.5K tokens, well
# inside short context — re-check if the prompt ever grows substantially.
# Anthropic rates from the model catalog, same date.
CHALLENGERS = {
    "sol":    dict(provider="openai",    model="gpt-5.6-sol",       price=(5.00, 30.00)),
    "terra":  dict(provider="openai",    model="gpt-5.6-terra",     price=(2.00, 12.00)),
    "luna":   dict(provider="openai",    model="gpt-5.6-luna",      price=(0.20,  1.20)),
    "sonnet": dict(provider="anthropic", model="claude-sonnet-4-6", price=(3.00, 15.00)),
    # Opus 5 is the production model, so the copied champion brief usually
    # stands in for it. Run it as an arm when the prompt has changed since
    # publication (a prompt or source-layer edit) — then the morning's published
    # brief was built from different input and is no longer a fair comparison.
    "opus":   dict(provider="anthropic", model="claude-opus-5",     price=(5.00, 25.00)),
    # DeepSeek V4, added 2026-07-31 (V4-Flash-0731 shipped that morning).
    # Prices from api-docs.deepseek.com/quick_start/pricing, same date. `price`
    # is the CACHE-MISS input rate; `cache_price` is the cache-hit rate, which
    # DeepSeek prices 50x lower and which run_one applies when the response
    # reports a hit. Both models are 1M context / 384K max output and think by
    # default — left on, since the champion (Opus 5) does too.
    # ⚠ Peak-hour surcharge: 2x on all billing items during Beijing 09:00-12:00
    # and 14:00-18:00, i.e. 18:00-21:00 and 23:00-03:00 MST. The 6:05 AM MST
    # cron lands at 21:05 Beijing — off-peak. Ad-hoc evening runs may not be.
    # max_tokens: the V4 arms think hard enough to exhaust the shared 16K budget
    # on reasoning alone and return `finish=length` with EMPTY text (measured
    # 2026-07-31: 16,000/16,000 output tokens were thinking). Their ceiling is
    # 384K, and output is $0.28/MTok, so the headroom is nearly free.
    "flash":  dict(provider="deepseek", model="deepseek-v4-flash",
                   price=(0.14, 0.28), cache_price=0.0028, max_tokens=48000),
    "dspro":  dict(provider="deepseek", model="deepseek-v4-pro",
                   price=(0.435, 0.87), cache_price=0.003625, max_tokens=48000),
    # Grok 4.6, added 2026-08-12 (launch day). Model id verified against
    # api.x.ai/v1/models the same day; prices from docs.x.ai + Artificial
    # Analysis: $2/$6, cache hits $0.50/MTok. The cache discount is only 4x —
    # not priced separately, same rule as OpenAI above (and the daily prompt
    # never repeats anyway). Reasoning model: thinking bills as output and
    # counts against max_tokens, so it gets DeepSeek-style headroom instead of
    # the shared 16K. xAI speaks the OpenAI chat wire format (reasoning under
    # completion_tokens_details.reasoning_tokens), so run_chat handles it.
    "grok":   dict(provider="xai", model="grok-4.6",
                   price=(2.00, 6.00), max_tokens=48000),
}
CHEAP = ["luna", "terra", "flash"]
# The champion is the *published* brief (generate_brief.py CLAUDE_MODEL), not a
# challenger — it is not re-run here, just copied in for side-by-side reading.
CHAMPION_PRICE = (5.00, 25.00)


def capture_prompt():
    import generate_brief as gb
    captured = {}

    def spy(prompt, api_key, max_tokens=None):
        captured["prompt"] = prompt
        return None

    real, gb.call_claude = gb.call_claude, spy
    argv, sys.argv = sys.argv, ["generate_brief.py"]
    try:
        gb.main()
    except SystemExit:
        pass
    finally:
        gb.call_claude, sys.argv = real, argv
    if not captured.get("prompt"):
        raise RuntimeError("could not capture the synthesis prompt")
    return captured["prompt"]


def _post(url, body, headers, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run_chat(spec, prompt, keys):
    """OpenAI ChatCompletions wire format — OpenAI and DeepSeek both speak it."""
    p = PROVIDERS[spec["provider"]]
    data = _post(p["url"], {
        "model": spec["model"],
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
        # OpenAI reports cached input under prompt_tokens_details.cached_tokens;
        # DeepSeek uses a top-level prompt_cache_hit_tokens. Only priced in when
        # the spec carries a cache_price (see run_one).
        "cache_hit": (u.get("prompt_cache_hit_tokens")
                      or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        "thinking": thinking,
        "finish": choice.get("finish_reason"),
    }


def run_anthropic(spec, prompt, keys):
    data = _post(PROVIDERS["anthropic"]["url"], {
        "model": spec["model"],
        "max_tokens": spec.get("max_tokens", MAX_TOKENS),
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
        "thinking": 0,
        "finish": data.get("stop_reason"),
    }


RUNNERS = {"openai": run_chat, "deepseek": run_chat, "xai": run_chat,
           "anthropic": run_anthropic}


def run_one(key, spec, prompt, keys):
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
    # Cached input is only priced separately for providers where the discount is
    # large enough to distort the comparison (DeepSeek: 50x). Leaving the others
    # at the full input rate keeps their numbers comparable to earlier ab.jsonl
    # rows, at the cost of slightly overstating them.
    hit = (meta.get("cache_hit") or 0) if "cache_price" in spec else 0
    miss = (meta.get("in") or 0) - hit
    meta["secs"] = round(time.time() - t0, 1)
    meta["usd"] = round((miss * pin + hit * spec.get("cache_price", pin)
                         + (meta.get("out") or 0) * pout) / 1e6, 4)
    # A billed call that returns no text is a failure, not a success. Without
    # this the run counter reads "1/1 arms succeeded" for a run that wrote no
    # brief — which is exactly what a thinking model does when it exhausts
    # max_tokens on reasoning (finish=length, empty content).
    if not text:
        meta["error"] = f"empty response (finish={meta.get('finish')}, " \
                        f"{meta.get('thinking') or 0} thinking tokens)"
    return text, meta


def gate(text, prompt):
    """Provenance gate in shadow mode — the one automated quality signal here.
    Never writes to the gate's own log; that file is production's."""
    try:
        import provenance_gate as pg
        return pg.check(text, prompt, mode=pg.Mode.SHADOW).summary()
    except Exception as e:
        return f"unavailable: {e}"


def champion_brief(today):
    """The published brief, for side-by-side reading. Not re-run — copied."""
    p = BRIEF_DIR / f"tucson-brief-{today}.md"
    return p.read_text() if p.exists() else None


def write_comparison(today, rows, champ_words):
    """One file to read them all against each other."""
    lines = [f"# Model bake-off — {today}", "",
             "Byte-identical prompt; only the model varies. "
             "Champion = the brief actually published this morning.", "",
             "| arm | model | words | heads | secs | $ | gate |",
             "|---|---|---|---|---|---|---|"]
    if champ_words is not None:
        lines.append(f"| **champion** | claude-opus-5 (published) | {champ_words} "
                     f"| — | — | — | see brief-inputs/shadow.jsonl |")
    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['arm']} | {r['model']} | — | — | {r.get('secs','—')} "
                         f"| — | ERROR: {r['error'][:60]} |")
        else:
            lines.append(f"| {r['arm']} | {r['model']} | {r.get('words')} "
                         f"| {r.get('headlines')} | {r.get('secs')} | {r.get('usd')} "
                         f"| {r.get('gate','n/a')} |")
    lines += ["", "## How to read this", "",
              "Cost and latency are free to capture and are not the question. "
              "Read the briefs themselves: which stories each picked, which it "
              "missed, and whether any asserted something the sources do not "
              "support. An **UNGROUNDED** gate finding is the strongest single "
              "signal — the model named something the sources never contained.",
              "", "Files:", ""]
    if champ_words is not None:
        lines.append(f"- `{today}-champion-claude-opus-5.md`")
    for r in rows:
        if not r.get("error"):
            lines.append(f"- `{today}-{r['model']}.md`")
    (OUT_DIR / f"{today}-comparison.md").write_text("\n".join(lines) + "\n")


def send_to_telegram(today, rows, champ_words):
    """ONE consolidated summary — not five briefs.

    This does NOT breach the "brief goes out once" rule in PIPELINE.md. That rule
    exists so the *published* brief is not sent twice; this is a different
    artifact that was never published. Five full briefs every morning would get
    the channel muted, which is how an alert channel stops protecting anything —
    so this sends the scoreboard and leaves the reading to brief-bake-off/.
    """
    if not SEND_TELEGRAM.exists():
        print("  WARN: send_telegram.py not found; skipping", file=sys.stderr)
        return
    body = [f"🧪 MODEL BAKE-OFF — {today} — NOT PUBLISHED", ""]
    if champ_words is not None:
        body.append(f"champion  claude-opus-5   {champ_words}w  (published)")
    for r in sorted(rows, key=lambda x: x.get("usd") or 0):
        if r.get("error"):
            body.append(f"{r['arm']:8s} {r['model']:18s} ERROR {r['error'][:40]}")
        else:
            body.append(f"{r['arm']:8s} {r['model']:18s} {r.get('words')}w  "
                        f"{r.get('secs')}s  ${r.get('usd')}")
            body.append(f"         gate: {r.get('gate','n/a')}")
    body += ["", f"Read side by side: brief-bake-off/{today}-comparison.md"]

    # send_telegram.py takes a FILE PATH (sys.argv[1] -> md_path), not message
    # text. Passing text directly makes it open a long string as a filename; it
    # fails, and without checking the exit code we would report success anyway.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write("\n".join(body))
        tmp = fh.name
    try:
        sys.stdout.flush()
        r = subprocess.run([sys.executable, str(SEND_TELEGRAM), tmp],
                           check=False, timeout=120)
        print("  Telegram: bake-off summary sent" if r.returncode == 0
              else f"  WARN: telegram exited {r.returncode} — NOT delivered",
              file=sys.stderr)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"  WARN: telegram send failed: {e}", file=sys.stderr)
    finally:
        os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help=f"comma-separated subset of {','.join(CHALLENGERS)}")
    ap.add_argument("--cheap", action="store_true", help=f"only {','.join(CHEAP)}")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()

    arms = CHEAP if a.cheap else (
        [m.strip() for m in a.models.split(",")] if a.models else list(CHALLENGERS))
    unknown = [m for m in arms if m not in CHALLENGERS]
    if unknown:
        sys.exit(f"ERROR: unknown arm(s) {unknown}; known: {list(CHALLENGERS)}")

    keys = {p: os.environ.get(cfg["env"]) for p, cfg in PROVIDERS.items()}
    for provider in {CHALLENGERS[m]["provider"] for m in arms}:
        if not keys[provider]:
            sys.exit(f"ERROR: {PROVIDERS[provider]['env']} not set. Cron does not "
                     f"read ~/.bashrc — the key must live in "
                     f"~/.config/environment.d/.")

    OUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = capture_prompt()
    print(f"\nprompt captured: {len(prompt):,} chars; arms: {', '.join(arms)}\n",
          file=sys.stderr)

    champ = champion_brief(today)
    champ_words = len(champ.split()) if champ else None
    if champ:
        (OUT_DIR / f"{today}-champion-claude-opus-5.md").write_text(champ)
        print(f"  champion copied in ({champ_words} words)", file=sys.stderr)
    else:
        print("  WARN: no published brief found to compare against", file=sys.stderr)

    rows = []
    for arm in arms:
        spec = CHALLENGERS[arm]
        print(f"  running {arm} ({spec['model']}) …", file=sys.stderr)
        text, meta = run_one(arm, spec, prompt, keys)
        meta.update(arm=arm, model=spec["model"], date=today,
                    prompt_chars=len(prompt))
        if text:
            (OUT_DIR / f"{today}-{spec['model']}.md").write_text(text)
            meta["words"] = len(text.split())
            meta["headlines"] = sum(1 for ln in text.splitlines()
                                    if ln.startswith("**"))
            meta["gate"] = gate(text, prompt)
            print(f"     {meta['words']}w  {meta['secs']}s  ${meta['usd']}  "
                  f"gate: {meta['gate']}", file=sys.stderr)
        else:
            print(f"     FAILED: {meta.get('error')}", file=sys.stderr)
        rows.append(meta)

    with open(OUT_DIR / "ab.jsonl", "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    write_comparison(today, rows, champ_words)

    ok = [r for r in rows if not r.get("error")]
    total = sum(r.get("usd") or 0 for r in ok)
    print(f"\n{len(ok)}/{len(rows)} arms succeeded; run cost ${total:.4f}",
          file=sys.stderr)
    print(f"Wrote {OUT_DIR / f'{today}-comparison.md'}", file=sys.stderr)

    if not a.no_telegram and ok:
        send_to_telegram(today, rows, champ_words)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
