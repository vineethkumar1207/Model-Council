"""Inspect Round 2 raw comparator output from most recent sessions - detailed."""
import json, glob, os

sessions_dir = "sessions"
files = sorted(glob.glob(os.path.join(sessions_dir, "MC-*.json")), key=os.path.getmtime, reverse=True)

for fpath in files[:3]:
    fname = os.path.basename(fpath)
    with open(fpath, "r", encoding="utf-8") as f:
        s = json.load(f)
    
    print(f"\n{'='*80}")
    print(f"Session: {fname}")
    
    for r in s.get("rounds", []):
        rn = r.get("round")
        if rn != 2:
            continue
        
        structured = r.get("structured", {})
        print(f"  parse_status: {structured.get('parse_status')}")
        print(f"  parse_warnings: {structured.get('parse_warnings')}")
        print(f"  claim_assessments: {len(structured.get('claim_assessments', []))}")
        print(f"  disagreements: {len(structured.get('disagreements', []))}")
        
        for model_id, output in r.get("raw_outputs", {}).items():
            print(f"\n  Model: {model_id}")
            print(f"  contribution_status: {output.get('contribution_status')}")
            print(f"  done_reason: {output.get('done_reason')}")
            print(f"  output_tokens: {output.get('output_tokens')}")
            print(f"  input_tokens: {output.get('input_tokens')}")
            resp = output.get("response", "")
            print(f"  response length (chars): {len(resp)}")
            # Show LAST 500 chars to see if truncated
            print(f"  --- LAST 500 CHARS ---")
            print(resp[-500:])
            print(f"  --- END ---")
            # Check if response starts with ```json
            print(f"  starts_with_json_fence: {resp.strip().startswith('```json')}")
            print(f"  starts_with_brace: {resp.strip().startswith('{')}")
            # Try parse_json_or_none
            from council import parse_json_or_none, parse_json_strict
            parsed = parse_json_or_none(resp)
            print(f"  parse_json_or_none result: {'dict' if isinstance(parsed, dict) else type(parsed).__name__}")
            if isinstance(parsed, dict):
                print(f"    keys: {list(parsed.keys())}")
                print(f"    claim_assessments: {len(parsed.get('claim_assessments', []))}")
                print(f"    disagreements: {len(parsed.get('disagreements', []))}")
            strict = parse_json_strict(resp)
            print(f"  parse_json_strict result: {'dict' if isinstance(strict, dict) else type(strict).__name__ if strict is not None else 'None'}")
