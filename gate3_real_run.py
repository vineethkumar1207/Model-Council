import argparse
import json
import os
import sys

import config_loader
from pathlib import Path

# Gate 3 Models
MODELS = ["llama3.2:latest", "qwen2.5:7b", "minimax-m3:cloud"]

def run_gate3():
    # We set a controlled task
    topic = "Analyze the long-term feasibility and ecological impact of transitioning global supply chains entirely to circular economy models."

    engine, store, renderer = config_loader.build_engine(Path("."))
    
    print("=== Step 2: Verify Candidate Models ===")
    
    # We build the model dicts expected by the engine
    model_dicts = []
    for m in MODELS:
        provider = "gemini" if "gemini" in m else ("minimax" if "minimax" in m else "ollama")
        locality = "cloud" if "cloud" in m or provider == "gemini" else "local"
        if "cloud" in m and provider == "ollama":
            provider = "ollama" # or whatever the provider is mapped to
            # actually minimax-m3:cloud is an ollama model in this project
        model_dicts.append({"id": m, "provider": "ollama" if provider=="minimax" else provider, "locality": locality})
        
    health_report, healthy_models = engine.health_check_selected_models(model_dicts)
    
    for m in MODELS:
        info = health_report.get(m, {})
        status = info.get("status", "UNAVAILABLE/UNAUTHORIZED")
        print(f"Model ID: {m} | Provider: ollama | READY state: {status == 'READY'} | Health result: {status}")

    ready_models = [m["id"] for m in healthy_models if m["id"] in MODELS]
    
    if len(ready_models) < 3:
        print("\nGATE 3 = BLOCKED / NOT PROVEN (Not all required models are healthy/ready)")
        sys.exit(1)
        
    print("\n=== Step 3: Verify Three Distinct Model Identity ===")
    print(f"len(unique(model_ids)) == {len(set(ready_models))} | Models: {set(ready_models)}")
    
    def strict_roles(healthy):
        return {
            "experimental": True,
            "reason": "Gate 3 allocation",
            "analyst": ["llama3.2:latest", "qwen2.5:7b", "minimax-m3:cloud"],
            "comparator": ["qwen2.5:7b"],
            "challenger": ["minimax-m3:cloud"],
            "synthesizer": ["llama3.2:latest"]
        }

    approved_role_plan = strict_roles(healthy_models)
    session = store.create(
        "Gate 3 Deliberation",
        [m["id"] for m in healthy_models],
        role_plan=approved_role_plan
    )
    
    print("\n=== Step 5 & 6-17: Executing Bounded Real-Model Run ===")
    try:
        session = engine.run(
            session,
            topic,
            healthy_models,
            role_plan=approved_role_plan,
            renderer=renderer
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    session_id = session.get("session_id")
    print(f"\n=== Execution Complete ===")
    print(f"Session ID: {session_id}")
    print(f"Session Status: {session.get('current_state', {}).get('status')}")
    
    print("\n=== Step 14 & 15: Validate Telemetry ===")
    print(f"Session telemetry: {json.dumps(session.get('telemetry', {}), indent=2)}")
    
    return session_id

if __name__ == "__main__":
    run_gate3()
