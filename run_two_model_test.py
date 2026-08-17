"""
Two-model Council integration test.
  Cloud : minimax-m3:cloud
  Local : zephyr:7b-alpha-q3_K_S

Runs a full 4-round Council deliberation with console rendering and
saves the session JSON for later inspection.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from council import CouncilEngine
from providers.ollama import OllamaProvider
from renderer import TerminalRenderer
from session_store import SessionStore

# ── configuration ─────────────────────────────────────────────────────────────
CLOUD_MODEL = "minimax-m3:cloud"
LOCAL_MODEL  = "zephyr:7b-alpha-q3_K_S"
SELECTED     = [CLOUD_MODEL, LOCAL_MODEL]

QUESTION = (
    "For an AI system whose primary objective is reliability rather than maximum answer quality, "
    "is a multi-model Council actually better than using one strong model with deterministic tools? "
    "Take a clear position. Identify the strongest argument for the alternative, the conditions "
    "under which your position would fail, and the specific evidence that would change your conclusion."
)

CONFIG = {
    "ollama_base_url": "http://127.0.0.1:11434",
    "temperature": 0.7,
    "max_output_tokens": 2000,
    "health_check_timeout_seconds": 45,
    "min_healthy_models": 2,
    "council_diversity_required": True,
    "checkpoint_every_n_rounds": 1,
    "session_dir": str(ROOT / "sessions"),
}

SESSIONS_DIR = ROOT / "sessions"


def separator(title: str = "", width: int = 72) -> None:
    if title:
        pad = max(0, width - len(title) - 4)
        left = pad // 2
        right = pad - left
        print(f"\n{'-' * left}[ {title} ]{'-' * right}\n")
    else:
        print("-" * width)


def main() -> None:
    separator("TWO-MODEL COUNCIL TEST")
    print(f"  Cloud model : {CLOUD_MODEL}")
    print(f"  Local model : {LOCAL_MODEL}")
    print(f"  Question    : {QUESTION[:80]}...")
    separator()

    # ── provider ──────────────────────────────────────────────────────────────
    provider = OllamaProvider("http://127.0.0.1:11434")

    try:
        available = {m["id"] for m in provider.list_models()}
    except Exception as exc:
        print(f"\n[FATAL] Cannot reach Ollama: {exc}")
        sys.exit(1)

    missing = [m for m in SELECTED if m not in available]
    if missing:
        print(f"\n[FATAL] Required model(s) not available in Ollama: {missing}")
        print(f"  Available: {sorted(available)}")
        sys.exit(1)

    print(f"\n[OK] Both models confirmed available in Ollama.")

    # ── health check ──────────────────────────────────────────────────────────
    separator("HEALTH CHECK")
    health_results = {}
    for model_id in SELECTED:
        label = "cloud" if model_id == CLOUD_MODEL else "local"
        print(f"  Checking {model_id} ({label})...", end=" ", flush=True)
        try:
            r = provider.health_check(
                model_id,
                max_output_tokens=4,
                temperature=0.0,
                timeout_seconds=45,
            )
            health_results[model_id] = "READY"
            print(f"READY  (response: {str(r.get('response',''))[:40]!r})")
        except TimeoutError as exc:
            health_results[model_id] = "TIMEOUT"
            print(f"TIMEOUT  ({exc})")
        except Exception as exc:
            health_results[model_id] = "FAILED"
            print(f"FAILED  ({exc})")

    not_ready = [m for m, s in health_results.items() if s != "READY"]
    if not_ready:
        print(f"\n[FATAL] Model(s) not READY: {not_ready}")
        sys.exit(1)

    separator("COUNCIL DELIBERATION")
    print(f"  Starting full 4-round Council with {len(SELECTED)} models...\n")

    # ── engine ────────────────────────────────────────────────────────────────
    engine = CouncilEngine(
        CONFIG,
        {"ollama": provider},
        SessionStore(SESSIONS_DIR),
        renderer=TerminalRenderer(),
    )

    # Build a role plan: cloud=analyst, local=comparator
    # Role plan: dict keyed by role name, values are lists of model ID strings.
    # analyst = cloud (minimax), comparator = local (zephyr), synthesizer = fittest.
    role_plan = {
        "experimental": True,
        "reason": "Two-model integration test: cloud and local analysts + local comparator.",
        "analyst":     [CLOUD_MODEL, LOCAL_MODEL],
        "comparator":  [LOCAL_MODEL],
        "challenger":  [],
        "synthesizer": [CLOUD_MODEL],   # cloud model as synthesizer (best available)
    }

    session = engine.store.create(
        QUESTION,
        [CLOUD_MODEL, LOCAL_MODEL],
        role_plan=role_plan,
    )

    print(f"  Session ID  : {session['session_id']}")
    separator()

    # ── run ───────────────────────────────────────────────────────────────────
    import time
    t0 = time.perf_counter()
    try:
        models_for_run = [
            {"id": CLOUD_MODEL, "provider": "ollama"},
            {"id": LOCAL_MODEL,  "provider": "ollama"},
        ]
        result = engine.run(session, QUESTION, models_for_run, role_plan=role_plan)
    except Exception as exc:
        print(f"\n[FATAL] Council run failed: {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    elapsed = time.perf_counter() - t0

    # ── summary ───────────────────────────────────────────────────────────────
    separator("RUN SUMMARY")
    status = result.get("current_state", {}).get("status", "unknown")
    rounds = result.get("rounds", [])
    council_state = result.get("council_state", {})

    print(f"  Status          : {status}")
    print(f"  Rounds          : {len(rounds)}")
    print(f"  Claims          : {len(council_state.get('claims', []))}")
    print(f"  Disagreements   : {len(council_state.get('disagreements', []))}")
    print(f"  Role assignments: {len(council_state.get('role_assignments', []))}")
    print(f"  Total latency   : {elapsed*1000:.0f} ms  ({elapsed:.1f} s)")
    print(f"  Session ID      : {result.get('session_id', 'N/A')}")

    session_path = SESSIONS_DIR / f"{result.get('session_id', 'unknown')}.json"
    if session_path.exists():
        print(f"  Session file    : {session_path}")
    separator()

    # per-round assignments
    print("  Round-by-round execution:\n")
    for i, rnd in enumerate(rounds, 1):
        for asgn in rnd.get("assignments", []):
            model = asgn.get("actual_model", asgn.get("model", "?"))
            role  = asgn.get("role", "?")
            exst  = asgn.get("execution_status", "?")
            fb    = " [FALLBACK]" if asgn.get("fallback_used") else ""
            print(f"    Round {i} | {role:12s} | {model:30s} | {exst}{fb}")

    separator()
    if status == "completed":
        print("[PASS] Two-model Council test completed successfully.")
    else:
        print(f"[WARN] Council ended with status={status!r}. Check session file for details.")
    separator()


if __name__ == "__main__":
    main()
