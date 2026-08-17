"""
verify_real_consecutive.py
--------------------------
Runs exactly 3 consecutive, real, non-degraded two-model Council deliberations
using 1 cloud model and 1 local model, verifying validation, checkpoint, reload,
and session replay.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from council import CouncilEngine, InsufficientHealthyModels
from providers.ollama import OllamaProvider
from renderer import TerminalRenderer
from session_store import SessionStore

QUESTION = (
    "For a reliability-first AI coding system, should deterministic validation remain "
    "application-owned rather than delegated to the language model? Take a clear position. "
    "Give up to 3 claims, up to 2 assumptions, and up to 2 uncertainties."
)


def load_config():
    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def run_session(engine, selected_models, role_plan, session_idx):
    session_title = f"Consecutive Run {session_idx}"
    session = engine.store.create(QUESTION, [m["id"] for m in selected_models], role_plan=role_plan)
    
    print(f"\n--- Starting Deliberation: Session {session['session_id']} ---")
    t_start = time.perf_counter()
    
    result = engine.run(session, QUESTION, selected_models, role_plan=role_plan)
    duration = time.perf_counter() - t_start
    print(f"Deliberation finished in {duration:.2f} seconds.")
    
    return result, duration


def validate_result_and_replay(engine, result, duration, selected_models):
    # 1. State Validation
    status = result.get("current_state", {}).get("status")
    if status != "completed":
        print(f"[FAIL] Session status is {status!r}, expected 'completed'")
        return False
        
    rounds = result.get("rounds", [])
    if len(rounds) < 2:
        print(f"[FAIL] Expected at least 2 rounds, got {len(rounds)}")
        return False
        
    # Check Round 1
    r1 = rounds[0]
    if r1["role"] != "analyst" or r1["status"] != "completed":
        print("[FAIL] Round 1 analyst not completed")
        return False
    for asgn in r1["assignments"]:
        if asgn["contribution_status"] != "valid":
            print(f"[FAIL] Analyst {asgn['actual_model']} contribution is {asgn['contribution_status']!r}")
            return False
            
    # Check Claims
    claims = result.get("council_state", {}).get("claims", [])
    if not claims:
        print("[FAIL] Claims list is empty")
        return False
        
    # Check Round 2
    r2 = rounds[1]
    if r2["role"] != "comparator" or r2["status"] != "completed":
        print("[FAIL] Round 2 comparator not completed")
        return False
        
    # Check Round 4 (Synthesis)
    r4 = rounds[-1]
    if r4["role"] != "synthesizer" or r4["status"] != "completed":
        print("[FAIL] Round 4 synthesizer not completed")
        return False
        
    final_answer = result.get("current_state", {}).get("final_answer", {})
    if not final_answer.get("Decision") or not final_answer.get("Rationale"):
        print("[FAIL] Synthesizer final decision or rationale is empty")
        return False
        
    # 2. Checkpoint and Replay Validation
    session_id = result["session_id"]
    try:
        recovered = engine.store.load(session_id)
        if not recovered:
            print("[FAIL] Checkpoint loading returned None")
            return False
        if len(recovered["rounds"]) != len(rounds):
            print(f"[FAIL] Recovered rounds count mismatch: {len(recovered['rounds'])} vs {len(rounds)}")
            return False
    except Exception as e:
        print(f"[FAIL] Checkpoint loading threw error: {e}")
        return False
        
    print("[PASS] Validation & Checkpoint Replay checks completed successfully.")
    return True


def main():
    print("=" * 60)
    print("MODEL COUNCIL - CONSECUTIVE TWO-MODEL DELIBERATION VERIFICATION")
    print("=" * 60)
    
    config = load_config()
    # Explicitly configure healthy timeouts and tokens
    config["health_check_timeout_seconds"] = 8
    config["max_output_tokens"] = 1500
    config["min_healthy_models"] = 2
    
    provider = OllamaProvider(config["ollama_base_url"])
    try:
        models = provider.list_models()
    except Exception as exc:
        print(f"Ollama not reachable: {exc}")
        sys.exit(1)
        
    engine = CouncilEngine(config, {"ollama": provider}, SessionStore(ROOT / config["session_dir"]), renderer=TerminalRenderer())
    
    # Sort and discover cloud vs local candidates
    ordered = sorted(models, key=lambda m: (float(str(m.get("size", "0")).replace("GB", "").strip()) if m.get("size") else 999.0, m["id"]))
    cloud_candidates = [m for m in ordered if "cloud" in m["id"].lower()]
    local_candidates = [m for m in ordered if "cloud" not in m["id"].lower()]
    
    if not cloud_candidates or not local_candidates:
        print(f"Need at least 1 cloud and 1 local model candidate. Found cloud={len(cloud_candidates)}, local={len(local_candidates)}")
        sys.exit(1)
        
    print("Selected Candidates for Verification:")
    cloud_model = cloud_candidates[0]
    local_model = local_candidates[0]
    print(f"  Cloud model: {cloud_model['id']} [Locality: cloud]")
    print(f"  Local model: {local_model['id']} [Locality: local]")
    
    # Run Stage 1 & Stage 2 Health Checks
    print("\nRunning Candidate Health Checks...")
    health_results = {}
    for m in [cloud_model, local_model]:
        res = engine.health_check_model(m)
        health_results[m["id"]] = res
        print(f"  {m['id']}: status={res['health_status']} | latency={res['latency_ms']}ms | cold_start={res['cold_start']} | warmup_attempted={res['warmup_attempted']}")
        if res["health_status"] != "READY":
            print(f"Candidate {m['id']} is not READY: {res.get('reason')}")
            sys.exit(1)
            
    selected = [cloud_model, local_model]
    
    # Setup 2-model role plan
    role_plan = {
        "analyst": [cloud_model["id"], local_model["id"]],
        "comparator": [local_model["id"]],
        "challenger": [],
        "synthesizer": [cloud_model["id"]],
    }
    
    runs = []
    successes = 0
    
    for i in range(1, 4):
        print(f"\n=================== RUN {i} / 3 ===================")
        result, duration = run_session(engine, selected, role_plan, i)
        is_valid = validate_result_and_replay(engine, result, duration, selected)
        
        # Collect telemetry
        r1_outputs = result["rounds"][0]["raw_outputs"]
        latency_info = {}
        tokens_info = {}
        for m_id, out in r1_outputs.items():
            latency_info[m_id] = out.get("latency_ms") or out.get("latency_detail", {}).get("total_ms")
            tokens_info[m_id] = out.get("output_tokens")
            
        c_state = result.get("council_state", {})
        run_record = {
            "session_id": result["session_id"],
            "cloud_model": cloud_model["id"],
            "local_model": local_model["id"],
            "health": {m["id"]: health_results[m["id"]]["health_status"] for m in selected},
            "warmup": {m["id"]: health_results[m["id"]]["warmup_attempted"] for m in selected},
            "rounds": len(result["rounds"]),
            "claims": len(c_state.get("claims", [])),
            "agreements": sum(1 for d in c_state.get("disagreements", []) if d.get("status") == "resolved"),
            "disagreements": sum(1 for d in c_state.get("disagreements", []) if d.get("status") == "open"),
            "uncertainties": len(c_state.get("uncertainties", [])),
            "synthesis_status": "valid" if is_valid else "invalid",
            "decision_non_empty": bool(result.get("current_state", {}).get("final_answer", {}).get("Decision")),
            "checkpoint": is_valid,
            "replay": is_valid,
            "total_latency_seconds": round(duration, 2),
            "analyst_latencies_ms": latency_info,
            "analyst_tokens": tokens_info,
            "final_status": result["current_state"].get("status"),
        }
        runs.append(run_record)
        
        if is_valid:
            successes += 1
        else:
            print(f"\n[ABORT] Run {i} failed validation. Stopping sequence.")
            break
            
    print("\n" + "=" * 60)
    print("FINAL SUMMARY REPORT")
    print("=" * 60)
    print(f"Consecutive Successes: {successes} / 3")
    
    report = {
        "consecutive_successes": successes,
        "runs": runs,
    }
    
    report_path = ROOT / "verify_real_consecutive_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Saved consecutive run report to: {report_path}")
    
    if successes == 3:
        print("\n[PROMOTION CRITERION MET] 3/3 consecutive non-degraded deliberations passed.")
        sys.exit(0)
    else:
        print("\n[PROMOTION BLOCKED] Could not complete 3/3 consecutive non-degraded deliberations.")
        sys.exit(1)


if __name__ == "__main__":
    main()
