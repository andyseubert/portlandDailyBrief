"""Effective-dated rule checks do not override the published action."""
from collections import Counter
from datetime import date
import re

POSITIONS = {"yea", "nay", "abstain", "excused", "disqualified", "absent", "unknown"}
POSITION_MAP = {"aye": "yea", "ayes": "yea", "yes": "yea", "yea": "yea", "nay": "nay", "nays": "nay", "no": "nay", "abstain": "abstain", "abstention": "abstain", "absent": "absent", "excused": "excused", "disqualified": "disqualified"}


def action_type(disposition, body_kind="council"):
    text = disposition.casefold().strip()
    ordered = [(r"withdraw", "withdrawal"), (r"second reading|first reading", "reading"), (r"recommend", "committee_recommendation"), (r"resched", "rescheduling"), (r"continu|postpon", "continuance"), (r"refer", "referral"), (r"introduc", "introduction"), (r"reject|fail|denied", "rejection"), (r"report.*accept|accepted|received", "report_acceptance"), (r"placed on file", "report_receipt"), (r"amend", "amendment"), (r"pass|adopt|approv", "adoption")]
    for pattern, normalized in ordered:
        if re.search(pattern, text):
            if normalized in {"adoption", "amendment"} and body_kind in {"committee", "committee_of_whole"}:
                return "committee_action"  # never city adoption
            if normalized == "amendment" and re.match(r"passed|adopted", text):
                return "adoption"  # final 'passed as amended' differs from motion amendment
            return normalized
    return "unknown"


def validate_vote(event, rules=None, membership=None):
    issues = []
    votes = event.get("individual_votes", [])
    identities = [v.get("person_id") for v in votes]
    if any(not x for x in identities):
        issues.append("unknown_voter_identity")
    if len(set(x for x in identities if x)) != len([x for x in identities if x]):
        issues.append("duplicate_voter_identity_or_positions")
    if any(v.get("position") not in POSITIONS for v in votes):
        issues.append("unknown_vote_position")
    counts = Counter(v.get("position") for v in votes)
    tallies = event.get("declared_tallies", {})
    for position, tally in tallies.items():
        if not isinstance(tally, int) or tally < 0:
            issues.append("invalid_declared_tally")
        elif votes and counts[position] != tally:
            issues.append("named_count_tally_mismatch:" + position)
    if event.get("named_votes_complete") and sum(tallies.values()) != len(votes):
        issues.append("declared_total_named_count_mismatch")
    if membership is not None:
        eligible = {r["person_id"] for r in membership}
        if any(pid not in eligible for pid in identities if pid):
            issues.append("voter_not_in_effective_membership")
    else:
        issues.append("historical_membership_not_verified")
    check = None
    if rules is None:
        issues.append("body_procedure_not_verified_for_date")
    else:
        kind = event.get("event_kind", "unknown")
        if kind == 'ordinary_ordinance' and re.search(r'\b(?:street vacation|vacate.*street|dispose.*(?:property|land)|property disposal)\b', event.get('title', ''), re.I):
            issues.append('special_ordinance_procedure_requires_review')
        yea = tallies.get("yea", counts["yea"])
        nay = tallies.get("nay", counts["nay"])
        participants = event.get("explicit_participants")
        quorum = event.get("explicit_present_count")
        if quorum is not None and quorum < rules["quorum"]:
            issues.append("quorum_conflict")
        if rules.get("abstention_allowed") is False and (tallies.get("abstain", 0) or counts["abstain"]):
            issues.append("body_disallows_abstention")
        if kind == "consent":
            if participants is None:
                issues.append("consent_participants_not_verified")
            else:
                check = yea == len(participants) and yea >= rules.get("consent_minimum", 0) and nay == 0
                if votes and set(identities) != set(participants):
                    issues.append("consent_participant_identity_mismatch")
        elif kind in rules.get("affirmative_thresholds", {}):
            threshold = rules["affirmative_thresholds"][kind]
            check = yea >= threshold
            if rules.get("mayoral_tiebreak") and kind in rules["mayoral_tiebreak"]["eligible_event_kinds"] and yea == 6:
                # Code treats absences/abstentions/vacancies as no for tie determination.
                issues.append("mayoral_tiebreak_requires_explicit_evidence")
                check = None
        else:
            issues.append("motion_specific_threshold_unverified")
        if event.get('action_type') == 'adoption' and re.search(r'as amended', event.get('official_disposition', ''), re.I) and kind == 'ordinary_ordinance':
            issues.append('final_reading_amendment_timing_requires_document_review')
        declared = event.get("published_outcome")
        if check is not None and declared in {"passed", "failed"} and check != (declared == "passed"):
            issues.append("calculated_outcome_conflicts_with_official_action")
    return {"review_status": "hold" if issues else "validated", "issues": sorted(set(issues)), "calculated_passage_check": check, "published_outcome": event.get("published_outcome"), "counts": dict(counts)}


def validate_readings(introduction_date, final_date, rules, amendment_date=None):
    if not rules:
        return ["procedure_not_verified"]
    a, b = date.fromisoformat(introduction_date), date.fromisoformat(final_date)
    issues = []
    if (b - a).days < rules.get("minimum_reading_interval_days", 0):
        issues.append("readings_too_close")
    if amendment_date and (b - date.fromisoformat(amendment_date)).days < rules.get("amendment_interval_days", 0):
        issues.append("amendment_timing_requires_review")
    return issues


def effective_date(explicit_date=None, adoption_date=None, ordinance_class=None):
    # Explicit evidence wins. Do not guess which exception applies.
    return {"date": explicit_date, "method": "explicit_official_evidence" if explicit_date else "unknown_requires_document_review", "adoption_date": adoption_date, "ordinance_class": ordinance_class}
