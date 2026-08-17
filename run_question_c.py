"""
Run only Question C for Model Council validation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from council import CouncilEngine
from providers.ollama import OllamaProvider
from renderer import TerminalRenderer
from session_store import SessionStore

CLOUD_MODEL = "minimax-m3:cloud"
LOCAL_MODEL  = "zephyr:7b-alpha-q3_K_S"
SELECTED     = [CLOUD_MODEL, LOCAL_MODEL]

CONFIG = {
    "ollama_base_url": "http://127.0.0.1:11434",
    "temperature": 0.7,
    "max_output_tokens": 4096,
    "health_check_timeout_seconds": 45,
    "min_healthy_models": 2,
    "max_parallel_models": 1,
    "checkpoint_every_n_rounds": 1,
    "session_dir": str(ROOT / "sessions"),
}

QUESTION_C = (
    "Under what conditions can adding more models make an AI decision system less reliable rather than "
    "more reliable? Identify failure modes involving correlated errors, hallucinated consensus, latency, "
    "fallback, and verification."
)

def main():
    provider = OllamaProvider("http://127.0.0.1:11434")
    try:
        available = {m["id"] for m in provider.list_models()}
    except Exception as exc:
        print(f"Ollama list failed: {exc}")
        sys.exit(1)

    for m in SELECTED:
        if m not in available:
            print(f"Error: {m} is not installed.")
            sys.exit(1)

    store = SessionStore(ROOT / "sessions")
    engine = CouncilEngine(
        CONFIG,
        {"ollama": provider},
        store,
        renderer=TerminalRenderer(),
    )

    role_plan = {
        "experimental": True,
        "reason": "Validation test C: cloud & local analysts + cloud comparator.",
        "analyst":     [CLOUD_MODEL, LOCAL_MODEL],
        "comparator":  [CLOUD_MODEL],
        "challenger":  [],
        "synthesizer": [CLOUD_MODEL],
    }

    print("\n" + "="*80)
    print("  RUNNING QUESTION C")
    print("="*80)
    print(f"Question: {QUESTION_C}\n")

    models_for_run = [{"id": m, "provider": "ollama"} for m in SELECTED]
    session = store.create(QUESTION_C, SELECTED, role_plan=role_plan)
    print(f"Created session: {session['session_id']}")

    t0 = time.perf_counter()
    result = engine.run(session, QUESTION_C, models_for_run, role_plan=role_plan)
    elapsed = time.perf_counter() - t0

    print(f"\nCompleted in {elapsed:.1f} seconds.")
    print(f"Status        : {result['current_state'].get('status')}")
    print(f"Session status: {result['current_state'].get('session_status')}")
    print(f"Rounds        : {len(result.get('rounds', []))}")

    cs = result.get("council_state", {})
    print(f"Claims        : {len(cs.get('claims', []))}")
    print(f"Disagreements : {len(cs.get('disagreements', []))}")

    # Reload check
    reloaded = store.load(session["session_id"])
    assert reloaded is not None
    assert reloaded["current_state"]["status"] == result["current_state"]["status"]
    print("[OK] Checkpoint saved and reloaded successfully.")

    # Summary gate
    status = result["current_state"].get("status")
    rounds = len(result.get("rounds", []))
    if status == "completed" and rounds == 4:
        print("\n[PASS] Question C completed all 4 rounds successfully.")
    else:
        print(f"\n[FAIL] Expected status=completed rounds=4, got status={status} rounds={rounds}")

if __name__ == "__main__":
    main()
