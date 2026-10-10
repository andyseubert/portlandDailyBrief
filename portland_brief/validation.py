"""Structural/evidence checks reject obvious errors; they do not prove truth."""
import re
from .records import Story

INJECTION = re.compile(r"ignore (?:all |the |any )?(?:previous|prior|above) (?:instructions|prompts)|system prompt|reveal (?:your |the )?(?:secrets|credentials|api key)|execute (?:this |the )?(?:command|code)|you are now|<\|(?:system|assistant)\|>", re.I)


def suspicious(text):
    return bool(INJECTION.search(text))


def numbers(text):
    return set(re.findall(r"(?<![\w])\d+(?:[,.]\d+)*", text))


def normalize_space(value):
    return " ".join(value.casefold().split())


def supporting_spans(evidence):
    spans = {evidence.id + ":title": evidence.title}
    # Preserve exact original text; split long descriptions into bounded spans.
    chunks = re.split(r"(?<=[.!?])\s+(?=[A-Z])", evidence.text)
    index = 1
    for chunk in chunks:
        while chunk:
            part = chunk if len(chunk) <= 390 else chunk[:390].rsplit(" ", 1)[0]
            spans[evidence.id + f":s{index}"] = part
            index += 1
            chunk = chunk[len(part):].lstrip()
    return spans


def validate_story(raw, evidence):
    expected = {"headline", "sentences", "citation_ids", "evidence_spans"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("Model must return exactly the declared story fields")
    ids = raw["citation_ids"]
    if ids != [evidence.id]:
        raise ValueError("Fabricated, missing, or cross-story citation ID")
    headline = raw["headline"]
    sentences, spans = raw["sentences"], raw["evidence_spans"]
    if not isinstance(spans, list):
        raise ValueError("Evidence references must be an array")
    available = supporting_spans(evidence)
    spans = [available.get(s, s) if isinstance(s, str) else s for s in spans]
    if not isinstance(headline, str) or not 8 <= len(headline) <= 180:
        raise ValueError("Invalid headline")
    if not isinstance(sentences, list) or not 2 <= len(sentences) <= 3 or len(spans) != len(sentences):
        raise ValueError("Require two or three sentences and matching evidence spans")
    evidence_text = evidence.title + " " + evidence.text
    for sentence, span in zip(sentences, spans):
        if not isinstance(sentence, str) or not 20 <= len(sentence) <= 400 or sentence[-1:] not in ".!?":
            raise ValueError("Summary contains an invalid or incomplete sentence")
        if not isinstance(span, str) or not 12 <= len(span) <= 400:
            raise ValueError("Invalid supporting evidence span")
        if normalize_space(span) not in normalize_space(evidence_text):
            raise ValueError("Supporting span is not in fetched evidence")
    output = headline + " " + " ".join(sentences)
    if re.search(r"https?://|www\.|<[^>]*>", output) or suspicious(output):
        raise ValueError("Model output contains a URL, markup, or source instruction")
    if numbers(output) - numbers(evidence_text):
        raise ValueError("Summary introduces an unsupported number")
    # Longer verbatim passages belong in the source, not our summaries.
    source_tokens = re.findall(r"\w+", evidence_text.lower())
    output_tokens = re.findall(r"\w+", " ".join(sentences).lower())
    source_phrases = {tuple(source_tokens[i:i + 13]) for i in range(len(source_tokens) - 12)}
    if any(tuple(output_tokens[i:i + 13]) in source_phrases for i in range(len(output_tokens) - 12)):
        raise ValueError("Summary copies a long source passage; original wording required")
    flags = ["limited_context", "editorial_review_required"]
    if re.search(r"alleg|arrest|accus|court|lawsuit|police|shoot|ICE|sex abuse", evidence_text, re.I):
        flags.append("consequential_claims_check_attribution")
    return Story(headline, sentences, ids, spans, flags)
