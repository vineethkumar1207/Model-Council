import json
import re
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()


def compact_json(obj, limit=12000):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def normalize_text(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def split_bullets(text):
    items = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[\).\s-]+", "", line)
        if line:
            items.append(line)
    if not items and text and text.strip():
        items.append(text.strip())
    return items


def normalize_structured_text(value, preferred_keys=None):
    preferred_keys = preferred_keys or ("claim_text", "claim", "text")
    if isinstance(value, dict):
        for key in preferred_keys:
            if value.get(key):
                return str(value[key]).strip()
        return ""
    return str(value).strip()


def extract_section_text(text, header):
    cleaned_text = re.sub(r"(?im)^\s*\*\*([^*:]+):?\*\*\s*:?", r"\1:", text or "")
    pattern = re.compile(
        rf"(?ims)^\s*{re.escape(header)}(?:\s*\([^)]*\))?\s*:\s*(.*?)(?=^\s*[A-Za-z][A-Za-z \-/()0-9]+:\s*|\Z)"
    )
    match = pattern.search(cleaned_text)
    return match.group(1).strip() if match else ""


def split_key_values(text):
    values = {}
    for chunk in re.split(r"\s*\|\s*", text or ""):
        if not chunk:
            continue
        if "=" in chunk:
            key, value = chunk.split("=", 1)
        elif ":" in chunk:
            key, value = chunk.split(":", 1)
        else:
            continue
        values[key.strip().lower()] = value.strip()
    return values


def split_map_items(text):
    mapping = {}
    for chunk in re.split(r"\s*;\s*", text or ""):
        if not chunk:
            continue
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        mapping[key.strip()] = value.strip()
    return mapping


def parse_list_with_kv(text):
    items = []
    for raw in split_bullets(text):
        kv = split_key_values(raw)
        if kv:
            items.append(kv)
        else:
            items.append({"text": raw})
    return items


def parse_json_or_none(text):
    if not text:
        return None
    payload = text.strip()
    try:
        return json.loads(payload)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", payload, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    start = payload.find("{")
    end = payload.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(payload[start : end + 1])
        except Exception:
            pass
    if start != -1:
        json_text = payload[start:]
        for trim_end in [
            json_text.rfind("},"),
            json_text.rfind("}"),
            json_text.rfind("],"),
            json_text.rfind("]"),
        ]:
            if trim_end <= 0:
                continue
            candidate = json_text[: trim_end + 1]
            opens = candidate.count("[") - candidate.count("]")
            braces = candidate.count("{") - candidate.count("}")
            suffix = "]" * max(0, opens) + "}" * max(0, braces)
            try:
                result = json.loads(candidate + suffix)
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
    return None


def is_truncated_text(text):
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if stripped.startswith("{") or "```json" in stripped:
        if not (stripped.endswith("}") or stripped.endswith("```") or stripped.endswith("}\n```") or stripped.endswith("```\n")):
            return True
    dangling_endings = (",", "-", ":", "(", "...", " and", " or", " the", " with", " a", " an", " is", " are", " of", " to", " in", " for")
    lowered = stripped.lower()
    if any(lowered.endswith(d) for d in dangling_endings):
        return True
    return False


def parse_round_one(text):
    payload = parse_json_or_none(text)
    if isinstance(payload, dict):
        return {
            "position": str(payload.get("position", "")).strip(),
            "claims": [
                item
                for item in (normalize_structured_text(x, ("claim_text", "claim", "text")) for x in payload.get("claims", []))
                if item
            ],
            "assumptions": [
                item
                for item in (normalize_structured_text(x, ("assumption", "text")) for x in payload.get("assumptions", []))
                if item
            ],
            "uncertainties": [
                item
                for item in (normalize_structured_text(x, ("uncertainty", "text")) for x in payload.get("uncertainties", []))
                if item
            ],
            "confidence": _to_float(payload.get("confidence")),
            "raw": text,
        }

    position = extract_section_text(text, "Position")
    claims = split_bullets(extract_section_text(text, "Claims")) or split_bullets(extract_section_text(text, "Key claims"))
    assumptions = split_bullets(extract_section_text(text, "Assumptions"))
    uncertainties = split_bullets(extract_section_text(text, "Uncertainties"))
    confidence = _to_float(extract_section_text(text, "Confidence"))
    return {
        "position": position,
        "claims": claims,
        "assumptions": assumptions,
        "uncertainties": uncertainties,
        "confidence": confidence,
        "raw": text,
    }


def _parse_json_payload_round_two(payload, parse_warnings):
    assessments = _coerce_list(payload.get("claim_assessments", payload.get("consensus_claims", payload.get("claims", []))))
    disagreements = _coerce_list(payload.get("disagreements", payload.get("meaningful_disagreements", [])))
    challenge = _coerce_list(payload.get("claims_requiring_challenge", payload.get("claims_to_challenge", [])))
    assumptions = _coerce_list(payload.get("assumptions"))
    uncertainties = _coerce_list(payload.get("uncertainties", payload.get("uncertain_claims", [])))
    status = "COMPLETE" if (assessments or disagreements or challenge or uncertainties) else "PARTIAL"
    return {
        "claim_assessments": assessments,
        "disagreements": disagreements,
        "claims_requiring_challenge": challenge,
        "assumptions": assumptions,
        "uncertainties": uncertainties,
        "parse_status": status,
        "parse_warnings": parse_warnings,
    }


def _extract_round_two_sections(text):
    assessments_text = (
        extract_section_text(text, "Claim assessments") or
        extract_section_text(text, "Claim Assessments") or
        extract_section_text(text, "Consensus Claims") or
        extract_section_text(text, "Consensus claims") or
        extract_section_text(text, "Claims") or
        extract_section_text(text, "claims")
    )
    disagreements_text = (
        extract_section_text(text, "Disagreements") or
        extract_section_text(text, "Meaningful Disagreements") or
        extract_section_text(text, "Meaningful disagreements")
    )
    challenge_text = (
        extract_section_text(text, "Claims that require challenge") or
        extract_section_text(text, "Claims That Require Challenge") or
        extract_section_text(text, "Claims Requiring Challenge")
    )
    uncertainties_text = (
        extract_section_text(text, "Uncertainties") or
        extract_section_text(text, "Uncertain Claims") or
        extract_section_text(text, "Uncertain claims")
    )
    assumptions_text = extract_section_text(text, "Assumptions")
    return assessments_text, disagreements_text, challenge_text, uncertainties_text, assumptions_text


def _parse_non_json_payload_round_two(text, parse_warnings):
    assessments_text, disagreements_text, challenge_text, uncertainties_text, assumptions_text = _extract_round_two_sections(text)

    assessments = parse_list_with_kv(assessments_text) if assessments_text else []
    disagreements = parse_list_with_kv(disagreements_text) if disagreements_text else []
    challenge = parse_list_with_kv(challenge_text) if challenge_text else []
    uncertainties = parse_list_with_kv(uncertainties_text) if uncertainties_text else []
    assumptions = parse_list_with_kv(assumptions_text) if assumptions_text else []

    if not disagreements_text:
        parse_warnings.append("Disagreements section not explicitly found in headers")

    has_content = bool(assessments or disagreements or challenge or uncertainties or assumptions)
    parse_status = "COMPLETE" if has_content else "PARTIAL"

    return {
        "claim_assessments": assessments,
        "disagreements": disagreements,
        "claims_requiring_challenge": challenge,
        "assumptions": assumptions,
        "uncertainties": uncertainties,
        "parse_status": parse_status,
        "parse_warnings": parse_warnings,
    }


def parse_round_two(text):
    if not (text or "").strip():
        return {
            "claim_assessments": [],
            "disagreements": [],
            "claims_requiring_challenge": [],
            "assumptions": [],
            "uncertainties": [],
            "parse_status": "FAILED",
            "parse_warnings": ["Empty output"],
            "raw": text,
        }

    payload = parse_json_or_none(text)
    parse_warnings = []
    if isinstance(payload, dict):
        result = _parse_json_payload_round_two(payload, parse_warnings)
    else:
        result = _parse_non_json_payload_round_two(text, parse_warnings)
    result["raw"] = text
    return result


def parse_round_three(text):
    payload = parse_json_or_none(text)
    if isinstance(payload, dict):
        return {"revisions": _coerce_list(payload.get("revisions")), "raw": text}
    return {"revisions": parse_list_with_kv(extract_section_text(text, "Revisions")), "raw": text}


def parse_round_four(text):
    payload = parse_json_or_none(text)
    if isinstance(payload, dict):
        return {
            "Decision": str(payload.get("Decision", payload.get("decision", ""))).strip(),
            "Rationale": str(payload.get("Rationale", payload.get("rationale", ""))).strip(),
            "Conditions": _coerce_string_list(payload.get("Conditions", payload.get("conditions"))),
            "Trade-offs": _coerce_string_list(payload.get("Trade-offs", payload.get("trade-offs", payload.get("trade_offs")))),
            "Unresolved Issues": _coerce_string_list(payload.get("Unresolved Issues", payload.get("unresolved issues", payload.get("unresolved_issues")))),
            "Confidence": _to_float(payload.get("Confidence", payload.get("confidence"))),
            "raw": text,
        }

    return {
        "Decision": extract_section_text(text, "Decision"),
        "Rationale": extract_section_text(text, "Rationale"),
        "Conditions": split_bullets(extract_section_text(text, "Conditions")),
        "Trade-offs": split_bullets(extract_section_text(text, "Trade-offs")),
        "Unresolved Issues": split_bullets(extract_section_text(text, "Unresolved Issues")),
        "Confidence": _to_float(extract_section_text(text, "Confidence")),
        "raw": text,
    }


def resolve_participant_id(m, known_participant_ids):
    if not m or not known_participant_ids:
        return None
    if m in known_participant_ids:
        return m
    lowered_m = str(m).strip().lower()
    if lowered_m in ("model", "agent", "participant", "bot", "ai"):
        return None
    matches = []
    for pid in known_participant_ids:
        lowered_pid = str(pid).strip().lower()
        if lowered_m in lowered_pid or lowered_pid.startswith(lowered_m) or lowered_m.split(":")[0] == lowered_pid.split(":")[0] or lowered_m.split("-")[0] == lowered_pid.split("-")[0]:
            matches.append(pid)
    if len(matches) == 1:
        return matches[0]
    return None


def _coerce_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"text": str(item)} for item in value]
    if isinstance(value, dict):
        return [value]
    return [{"text": str(value)}]


def _coerce_string_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return split_bullets(str(value))


def _to_float(value, default=None):
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(value))
        return float(match.group(1)) if match else default


def profile_for_model(model_id, provider_name=None):
    text = f"{model_id} {provider_name or ''}".lower()
    if "qwen" in text:
        return "comparator"
    if "llama" in text:
        return "challenger"
    if "gemini" in text:
        return "synthesizer"
    return "analyst"


def normalize_position(text):
    lowered = normalize_text(text)
    if not lowered:
        return "unknown"
    if any(token in lowered for token in ["depends on", "depends", "conditional", "case by case", "context", "specific needs", "if ", "provided that"]):
        return "conditional_support"
    if any(token in lowered for token in ["uncertain", "unclear", "unknown", "insufficient", "not enough information"]):
        return "uncertain"

    has_oppose = any(token in lowered for token in ["oppose", "disagree", "reject", "against", "not recommend", "do not use"])
    has_support = any(token in lowered for token in ["support", "agree", "recommend", "favor", "yes", "in favor"])

    if has_oppose and has_support:
        return "mixed"
    if has_oppose:
        return "oppose"

    if "local" in lowered and "cloud" not in lowered and "hybrid" not in lowered:
        return "local"
    if "cloud" in lowered and "local" not in lowered and "hybrid" not in lowered:
        return "cloud"
    if "hybrid" in lowered:
        return "hybrid"

    if has_support:
        return "support"

    return "support"


def evaluate_position_disagreement(pos_a, pos_b):
    p1 = normalize_position(pos_a)
    p2 = normalize_position(pos_b)

    if p1 in ("unknown", "uncertain") or p2 in ("unknown", "uncertain"):
        return False, "insufficient_evidence"

    if p1 == p2:
        return False, "agreement"

    support_types = {"support", "local", "cloud", "hybrid"}
    if (p1 in support_types and p2 == "oppose") or (p2 in support_types and p1 == "oppose"):
        return True, "material_disagreement"

    arch_stances = {"local", "cloud", "hybrid"}
    if p1 in arch_stances and p2 in arch_stances and p1 != p2:
        return True, "material_disagreement"

    if p1 == "conditional_support" or p2 == "conditional_support":
        return True, "conditional_disagreement"

    return False, "uncertain"


def material_disagreement(position_map):
    if not isinstance(position_map, dict) or len(position_map) < 2:
        return False
    norm_positions = [normalize_position(pos) for pos in position_map.values()]
    filtered = [p for p in norm_positions if p not in ("unknown", "uncertain")]
    if len(filtered) < 2:
        return False
    if len(set(filtered)) == 1:
        return False
    for i in range(len(filtered)):
        for j in range(i + 1, len(filtered)):
            is_disag, _ = evaluate_position_disagreement(filtered[i], filtered[j])
            if is_disag:
                return True
    return False


def classify_stance(text):
    return normalize_position(text)


def calculate_council_diversity(role_assignments):
    requested_seats = len(role_assignments)
    actual_models = [a.get("actual_model") or a.get("assigned_model") for a in role_assignments if a.get("actual_model") or a.get("assigned_model")]
    independent_actual_models = len(set(actual_models))
    diversity_degraded = (independent_actual_models < requested_seats) if requested_seats > 1 else False
    duplicate_actual_models = list(set([m for m in actual_models if actual_models.count(m) > 1]))
    fallback_used_any = any(a.get("fallback_used") for a in role_assignments)

    return {
        "requested_seats": requested_seats,
        "actual_participants": len(actual_models),
        "independent_actual_models": independent_actual_models,
        "diversity_degraded": diversity_degraded,
        "duplicate_actual_models": duplicate_actual_models,
        "epistemic_degradation": diversity_degraded or fallback_used_any,
        "degradation_reason": f"Only {independent_actual_models} independent model(s) available for {requested_seats} requested seat(s)." if diversity_degraded else None,
    }


def parse_json_strict(text):
    if not text or not text.strip():
        return None
    payload = text.strip()
    try:
        return json.loads(payload)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", payload, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    start = payload.find("{")
    end = payload.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(payload[start : end + 1])
        except Exception:
            pass
    return None


def contribution_status_for(role, text, parsed, done_reason=None):
    if not (text or "").strip():
        return "empty"
    if text.strip().startswith("{") and parse_json_or_none(text) is None:
        if done_reason == "length":
            return "partial"
        return "malformed"
    if role == "analyst":
        if parsed.get("position") and parsed.get("claims"):
            return "valid"
    elif role == "comparator":
        if parsed.get("claim_assessments") or parsed.get("disagreements"):
            return "valid"
    elif role == "challenger":
        if parsed.get("revisions"):
            return "valid"
    elif role == "synthesizer":
        required = ("Decision", "Rationale", "Unresolved Issues", "Confidence")
        has_all_required = all(parsed.get(key) not in (None, "", []) for key in required)
        if has_all_required:
            return "valid"
    if is_truncated_text(text) or done_reason == "length":
        return "partial"
    return "partial"


def _coerce_id_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(key).strip() for key in value.keys() if str(key).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,;]", text) if item.strip()]
