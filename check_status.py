"""Quick status check on the three most recent sessions from the last run."""
import json, glob, os

files = ["sessions/MC-AC378CF4.json", "sessions/MC-6A3F1349.json", "sessions/MC-7DBCB9ED.json"]
for fpath in files:
    if not os.path.exists(fpath):
        print(f"Not found: {fpath}")
        continue
    with open(fpath, "r", encoding="utf-8") as f:
        s = json.load(f)
    cs = s.get("current_state", {})
    rounds = s.get("rounds", [])
    cstate = s.get("council_state", {})
    print(f"{os.path.basename(fpath)}: status={cs.get('status')}, session_status={cs.get('session_status')}, rounds={len(rounds)}, claims={len(cstate.get('claims',[]))}, disagreements={len(cstate.get('disagreements',[]))}")
