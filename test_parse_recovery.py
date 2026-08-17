"""Test that parse_json_or_none can recover from truncated code-fenced JSON."""
import json
from council import parse_json_or_none, parse_round_two, contribution_status_for

# Simulate the actual truncated response from MC-FEA5BED3
# Response was ```json\n{...claim_assessments...disagreements: [],\n  (truncated)
truncated_response = '''```json
{
  "claim_assessments": [
    {
      "claim_id": "C-001",
      "verification_status": "supported",
      "supporting_models": ["minimax-m3:cloud"],
      "contradicting_models": [],
      "confidence": 0.8,
      "notes": "Well-grounded claim."
    },
    {
      "claim_id": "C-002",
      "verification_status": "supported",
      "supporting_models": ["minimax-m3:cloud"],
      "contradicting_models": [],
      "confidence": 0.75,
      "notes": "Operational restatement."
    }
  ],
  "disagreements": [],
  "assumptions": [
    {
      "assumption_id": "A-001",
      "assumption": "The system has bounded latency"'''

# Test 1: parse_json_or_none should recover partial JSON
result = parse_json_or_none(truncated_response)
print(f"parse_json_or_none result type: {type(result)}")
if isinstance(result, dict):
    print(f"  keys: {list(result.keys())}")
    print(f"  claim_assessments: {len(result.get('claim_assessments', []))}")
    print(f"  disagreements: {len(result.get('disagreements', []))}")
else:
    print(f"  FAILED to parse: {result}")

# Test 2: parse_round_two should extract claim_assessments
parsed = parse_round_two(truncated_response)
print(f"\nparse_round_two result:")
print(f"  parse_status: {parsed.get('parse_status')}")
print(f"  claim_assessments: {len(parsed.get('claim_assessments', []))}")
print(f"  disagreements: {len(parsed.get('disagreements', []))}")

# Test 3: contribution_status_for with recovered content
status = contribution_status_for("comparator", truncated_response, parsed, done_reason="length")
print(f"\ncontribution_status_for: {status}")

# Test 4: Complete JSON should still work
complete_response = '''```json
{
  "claim_assessments": [{"claim_id": "C-001", "verification_status": "supported"}],
  "disagreements": [{"disagreement_id": "D-001", "claim_id": "C-001"}]
}
```'''
result2 = parse_json_or_none(complete_response)
print(f"\nComplete JSON parse result: {type(result2)}")
if isinstance(result2, dict):
    print(f"  keys: {list(result2.keys())}")

parsed2 = parse_round_two(complete_response)
status2 = contribution_status_for("comparator", complete_response, parsed2, done_reason="stop")
print(f"  contribution_status_for (complete): {status2}")

print("\nAll tests passed!" if status in ("valid", "partial") and status2 == "valid" else "\nSome tests may have issues.")
