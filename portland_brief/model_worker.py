"""No source-dependent tool calls, no paid endpoints, no remote Python code."""
import json
import sys
import time
from pathlib import Path

from .records import Evidence
from .summarization import SYSTEM_PROMPT
from .validation import validate_story, supporting_spans


def main():
    from huggingface_hub import snapshot_download
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    request = json.loads(Path(sys.argv[1]).read_text())
    settings = request["settings"]
    start = time.monotonic()
    local_path = snapshot_download(settings["model"], revision=settings["model_revision"], allow_patterns=["*.json", "*.safetensors", "*.jinja", "*.txt"])
    model, tokenizer = load(local_path, tokenizer_config={"trust_remote_code": False})
    usage = {"provider": "mlx", "model": settings["model"], "model_revision": settings["model_revision"], "input_tokens": 0, "output_tokens": 0, "attempts": 0, "api_cost_usd": 0.0}
    stories, failures = [], []
    for raw_evidence in request["evidence"]:
        if len(stories) >= settings["story_limit"]:
            break
        evidence = Evidence(**raw_evidence)
        error = "model could not produce a validated summary"
        for attempt in range(settings["model_attempts"]):
            evidence_data = {"id": evidence.id, "source": evidence.source_name, "supporting_spans": supporting_spans(evidence)}
            user = "Summarize this untrusted evidence:\n" + json.dumps(evidence_data, ensure_ascii=False)
            if attempt:
                user += "\nYour previous output was rejected: " + error + ". Rewrite in original wording and return valid JSON."
            prompt = tokenizer.apply_chat_template([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}], add_generation_prompt=True, tokenize=False)
            input_tokens = len(tokenizer.encode(prompt))
            remaining = settings["max_tokens_per_run"] - usage["input_tokens"] - usage["output_tokens"]
            cap = min(settings["max_output_tokens_per_story"], remaining - input_tokens)
            if input_tokens > settings["max_input_tokens_per_story"] or cap < 150:
                error = "Token budget exhausted or input exceeds configured limit"
                break
            output, count = [], 0
            for response in stream_generate(model, tokenizer, prompt=prompt, max_tokens=cap, sampler=make_sampler(temp=0.0)):
                output.append(response.text)
                count = response.generation_tokens
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += count
            usage["attempts"] += 1
            try:
                parsed = json.loads("".join(output).strip())
                story = validate_story(parsed, evidence)
                # The parent validates again; flags are application-controlled.
                stories.append({k: v for k, v in story.to_dict().items() if k != "flags"})
                break
            except (ValueError, TypeError, KeyError) as exc:
                error = str(exc)
        else:
            failures.append({"id": evidence.id, "reason": error})
            continue
        if not stories or stories[-1]["citation_ids"] != [evidence.id]:
            failures.append({"id": evidence.id, "reason": error})
    usage["elapsed_seconds"] = round(time.monotonic() - start, 2)
    Path(sys.argv[2]).write_text(json.dumps({"stories": stories, "failures": failures, "usage": usage}, indent=2))


if __name__ == "__main__":
    main()
