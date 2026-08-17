"""Inspect the Question B session (MC-11A61D39) Round 2 failure."""
import json

with open("sessions/MC-11A61D39.json", "r", encoding="utf-8") as f:
    s = json.load(f)

print(f"Session: MC-11A61D39")
print(f"Status: {s.get('current_state', {}).get('session_status')}")
print(f"Epistemic: {s.get('current_state', {}).get('epistemic_status')}")

for r in s.get("rounds", []):
    rn = r.get("round")
    if rn != 2:
        continue
    structured = r.get("structured", {})
    print(f"\nRound 2:")
    print(f"  parse_status: {structured.get('parse_status')}")
    print(f"  parse_warnings: {structured.get('parse_warnings')}")
    print(f"  claim_assessments: {len(structured.get('claim_assessments', []))}")
    print(f"  disagreements: {len(structured.get('disagreements', []))}")
    
    for model_id, output in r.get("raw_outputs", {}).items():
        print(f"\n  Model: {model_id}")
        print(f"  contribution_status: {output.get('contribution_status')}")
        print(f"  done_reason: {output.get('done_reason')}")
        print(f"  output_tokens: {output.get('output_tokens')}")
        resp = output.get("response", "")
        print(f"  response length (chars): {len(resp)}")
        print(f"  starts_with: {repr(resp[:30])}")
        print(f"  ends_with: {repr(resp[-200:])}")
        
        from council import parse_json_or_none, is_truncated_text
        parsed = parse_json_or_none(resp)
        print(f"  parse_json_or_none: {'dict' if isinstance(parsed, dict) else 'None'}")
        if isinstance(parsed, dict):
            print(f"    keys: {list(parsed.keys())}")
            print(f"    claim_assessments: {len(parsed.get('claim_assessments', []))}")
        print(f"  is_truncated_text: {is_truncated_text(resp)}")
