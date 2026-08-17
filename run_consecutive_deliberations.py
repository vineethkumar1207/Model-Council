"""
Three consecutive deliberations runner for Model Council 2-model validation.
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

# Configuration
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

QUESTIONS = {
    "A": (
        "Should deterministic validation remain application-owned in an AI coding agent rather than "
        "delegated to the language model? Analyze reliability, observability, and failure containment."
    ),
    "B": (
        "For a reliability-first AI system, compare a single frontier model with deterministic tools "
        "against a multi-model Council. Identify where each architecture fails."
    ),
    "C": (
        "Under what conditions can adding more models make an AI decision system less reliable rather than "
        "more reliable? Identify failure modes involving correlated errors, hallucinated consensus, latency, "
        "fallback, and verification."
    )
}

def separator(title: str = ""):
    print("\n" + "=" * 80)
    if title:
        print(f"  {title}")
        print("=" * 80)

def run_question(label: str, question: str, engine: CouncilEngine, store: SessionStore):
    separator(f"RUNNING QUESTION {label}")
    print(f"Question: {question}\n")

    role_plan = {
        "experimental": True,
        "reason": f"Validation test {label}: cloud & local analysts + cloud comparator.",
        "analyst":     [CLOUD_MODEL, LOCAL_MODEL],
        "comparator":  [CLOUD_MODEL],
        "challenger":  [],
        "synthesizer": [CLOUD_MODEL],
    }

    session = store.create(question, SELECTED, role_plan=role_plan)
    session_id = session["session_id"]
    print(f"Created session: {session_id}")

    models_for_run = [{"id": m, "provider": "ollama"} for m in SELECTED]

    t0 = time.perf_counter()
    result = engine.run(session, question, models_for_run, role_plan=role_plan)
    elapsed = time.perf_counter() - t0

    print(f"\nCompleted in {elapsed:.1f} seconds.")
    print(f"Resulting Status: {result['current_state'].get('status')}")
    print(f"Rounds executed : {len(result.get('rounds', []))}")

    # Check reload
    reloaded = store.load(session_id)
    assert reloaded is not None, "Reloading failed!"
    assert reloaded["session_id"] == session_id
    assert reloaded["current_state"]["status"] == result["current_state"]["status"]
    print("[OK] Checkpoint saved and reloaded successfully.")
    
    return result

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

    results = {}
    for key, q in QUESTIONS.items():
        results[key] = run_question(key, q, engine, store)

    separator("ALL THREE RUNS COMPLETED")
    for key, res in results.items():
        state = res.get("council_state", {})
        claims = state.get("claims", [])
        disagreements = state.get("disagreements", [])
        print(f"Question {key}: status={res['current_state'].get('status')}, rounds={len(res.get('rounds', []))}, claims={len(claims)}, disagreements={len(disagreements)}")

if __name__ == "__main__":
    main()
