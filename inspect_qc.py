"""Inspect Question C (MC-2EEBF347) Round 2 failure."""
import json
from council import parse_json_or_none, is_truncated_text

with open("sessions/MC-2EEBF347.json", "r", encoding="utf-8") as f:
    s = json.load(f)

for r in s.get("rounds", []):
    rn = r.get("round")
    if rn != 2:
        continue
    structured = r.get("structured", {})
    print(f"Round 2:")
    print(f"  parse_status: {structured.get('parse_status')}")
    print(f"  claim_assessments: {len(structured.get('claim_assessments', []))}")
    print(f"  disagreements: {len(structured.get('disagreements', []))}")
    
    for model_id, output in r.get("raw_outputs", {}).items():
        resp = output.get("response", "")
        print(f"\n  Model: {model_id}")
        print(f"  contribution_status: {output.get('contribution_status')}")
        print(f"  done_reason: {output.get('done_reason')}")
        print(f"  output_tokens: {output.get('output_tokens')}")
        print(f"  response length (chars): {len(resp)}")
        print(f"  ends_with: {repr(resp[-300:])}")
        parsed = parse_json_or_none(resp)
        print(f"  parse_json_or_none: {'dict with keys=' + str(list(parsed.keys())) if isinstance(parsed, dict) else 'None'}")
        if isinstance(parsed, dict):
            print(f"    claim_assessments: {len(parsed.get('claim_assessments', []))}")
            print(f"    disagreements: {len(parsed.get('disagreements', []))}")
        print(f"  is_truncated_text: {is_truncated_text(resp)}")
