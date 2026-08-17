import argparse
import json
import os
from pathlib import Path

from council import CouncilEngine, InsufficientHealthyModels
from renderer import TerminalRenderer
from session_store import SessionStore
import config_loader

ROOT = Path(__file__).resolve().parent


def build_engine():
    return config_loader.build_engine(ROOT)


def print_models(engine):
    models = engine.discover_models()
    print("\nAvailable models\n")
    for i, m in enumerate(models, 1):
        print(f"{i:>2}. {m['id']:<28} [{m['provider']}] {m.get('size', '')}")
    if not models:
        print("No models found. Is Ollama running?")
    print()


def _prompt_role_plan(engine, renderer, selected):
    recommendation = engine.recommend_roles(selected)
    print(renderer.render_role_recommendation(recommendation))
    answer = input("Approve this experimental role plan? [Y]es / [N]o / [E]dit: ").strip().lower()
    if answer in {"", "y", "yes"}:
        return recommendation
    if answer in {"e", "edit"}:
        print(
            "Enter roles like: analyst=model1,model2; comparator=model3; challenger=model4; synthesizer=model5"
        )
        raw = input("> ").strip()
        if raw:
            override = config_loader.parse_role_override(raw, selected)
            print(renderer.render_role_recommendation(override))
            confirm = input("Approve edited plan? [Y]es / [N]o: ").strip().lower()
            if confirm in {"", "y", "yes"}:
                return override
    print("Role plan not approved yet. You can approve it again before asking a question.")
    return None


def _selected_summary(renderer, selected, approved_role_plan, health_report=None):
    print(renderer.render_selected_models(selected, approved_role_plan, health_report))


def _read_buffered_stdin(first_line):
    lines = [first_line]
    try:
        if sys.platform == "win32":
            import msvcrt
            while msvcrt.kbhit():
                line = sys.stdin.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
        else:
            import select
            while select.select([sys.stdin], [], [], 0.05)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
    except Exception:
        pass
    return "\n".join(lines).strip()


KNOWN_COMMANDS = {
    "models",
    "select",
    "selected",
    "roles",
    "new",
    "sessions",
    "resume",
    "ask",
    "status",
    "testmodel",
    "test-model",
    "benchmark",
    "export",
    "debug",
    "exit",
    "quit",
    "help",
    "history",
}


def print_session_history(hist_session):
    print(f"\n=== SESSION AUDIT: {hist_session['session_id']} ===")
    print(f"  Title   : {hist_session.get('title', '')}")
    print(f"  Status  : {hist_session.get('current_state', {}).get('session_status', '?')}")
    print(f"  Models  : {', '.join(hist_session.get('models', []))}")
    claims = hist_session.get('council_state', {}).get('claims', [])
    disagreements = hist_session.get('council_state', {}).get('disagreements', [])
    print(f"  Claims  : {len(claims)} | Disagreements: {len(disagreements)}")
    model_indep = hist_session.get('council_state', {}).get('model_independence', {})
    if model_indep.get('_summary'):
        summary = model_indep['_summary']
        print(f"  Independence: {summary.get('independent_analytical_sources', '?')} independent analyst(s)")
        if summary.get('role_reuse_models'):
            print(f"  [WARN] Role reuse: {', '.join(summary['role_reuse_models'])}")
    info_gain = hist_session.get('council_state', {}).get('information_gain', {})
    if info_gain.get('information_gain_score') is not None:
        print(f"  Info-gain: {info_gain['information_gain_score']} (EXPERIMENTAL)")
    print()
    for rnd in hist_session.get('rounds', []):
        print(f"  Round {rnd['round']} [{rnd['role'].upper()}] status={rnd['status']}")
        for asgn in rnd.get('assignments', []):
            req = asgn.get('requested_model', '?')
            act = asgn.get('actual_model') or asgn.get('assigned_model', '?')
            cstatus = asgn.get('contribution_status', '?')
            fallback = " [FALLBACK]" if asgn.get('fallback_used') else ""
            print(f"    {req} -> {act}{fallback} | contribution={cstatus}")
        for model_id, output in rnd.get('raw_outputs', {}).items():
            prompt = output.get('prompt', '')
            if prompt:
                preview = prompt[:120].replace('\n', ' ')
                print(f"    prompt({model_id}): {preview}...")
            tok_out = output.get('output_tokens')
            lat = output.get('latency_ms') or output.get('latency_detail', {}).get('total_ms')
            if tok_out or lat:
                print(f"    output_tokens={tok_out} | latency_ms={lat}")
    print()


def interactive(engine, store, renderer):
    selected = []
    current_session = None
    approved_role_plan = None

    print("Model Council - Raw Experiment v0.2")
    print("Type 'help' for commands.\n")

    while True:
        try:
            raw = input("Council > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return

        if not raw:
            continue

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in {"exit", "quit"}:
            return

        if cmd == "help":
            print(
                """
models                    Show installed/available models
select                    Select models interactively
selected                  Show current model selection
roles                     Review or approve experimental role plan
new [title]               Create a session
sessions                  List saved sessions
resume <id>               Resume a session
history <id>              Replay session audit trail (rounds, prompts, contributions)
ask <question>            Run a Council deliberation
status                    Show current state
benchmark <file>          Run benchmark dataset
export                    Print current session JSON
debug                     Alias for export
exit                      Quit
"""
            )
            continue

        if cmd == "models":
            print_models(engine)
            continue

        if cmd == "select":
            available = engine.discover_models()
            if not available:
                print("No models found.")
                continue
            print("\nChoose model numbers separated by commas:")
            for i, m in enumerate(available, 1):
                print(f"{i}. {m['id']} [{m['provider']}]")
            choice = input("> ").strip()
            try:
                indexes = [int(x.strip()) for x in choice.split(",") if x.strip()]
                selected = [available[i - 1] for i in indexes if 1 <= i <= len(available)]
                print()
                _selected_summary(renderer, selected, approved_role_plan)
                approved_role_plan = _prompt_role_plan(engine, renderer, selected)
            except ValueError:
                print("Invalid selection.")
            continue

        if cmd == "selected":
            if not selected:
                print("No models selected.")
            else:
                _selected_summary(renderer, selected, approved_role_plan, current_session.get("health_report") if current_session else None)
            continue

        if cmd == "roles":
            if not selected:
                print("Select models first.")
                continue
            approved_role_plan = _prompt_role_plan(engine, renderer, selected)
            continue

        if cmd == "new":
            title = arg or "Untitled Council Session"
            current_session = store.create(title, [m["id"] for m in selected], role_plan=approved_role_plan)
            print(f"Created {current_session['session_id']}")
            continue

        if cmd == "sessions":
            rows = store.list_sessions()
            if not rows:
                print("No sessions.")
            for s in rows:
                print(f"{s['session_id']}  {s['title']}  {s['updated_at']}")
            continue

        if cmd == "resume":
            if not arg:
                print("Usage: resume <session_id>")
                continue
            current_session = store.load(arg)
            if not current_session:
                print("Session not found.")
                continue
            selected = [{"id": x, "provider": engine.provider_for_model(x)} for x in current_session["models"]]
            approved_role_plan = current_session.get("approved_role_plan") or current_session.get("role_plan")
            print(renderer.render_status(current_session, selected, approved_role_plan))
            continue

        if cmd == "history":
            session_id = arg or (current_session.get("session_id") if current_session else None)
            if not session_id:
                print("Usage: history <session_id>  (or resume a session first)")
                continue
            hist_session = store.load(session_id)
            if not hist_session:
                print(f"Session not found: {session_id}")
                continue
            print_session_history(hist_session)
            continue

        if cmd == "status":
            print(renderer.render_status(current_session, selected, approved_role_plan))
            continue

        if cmd in {"export", "debug"}:
            if not current_session:
                print("No active session.")
            else:
                print(json.dumps(current_session, indent=2, ensure_ascii=False))
            continue

        if cmd in {"testmodel", "test-model"}:
            model_id = arg
            if not model_id and selected:
                model_id = selected[0]["id"]
            if not model_id:
                available = engine.discover_models()
                if not available:
                    print("No models available.")
                    continue
                print("Select a model number to test:")
                for i, m in enumerate(available, 1):
                    print(f"{i}. {m['id']} [{m['provider']}]")
                c = input("> ").strip()
                try:
                    idx = int(c) - 1
                    if 0 <= idx < len(available):
                        model_id = available[idx]["id"]
                except ValueError:
                    pass
            if not model_id:
                print("Usage: test-model <model_id>")
                continue
            provider_name = engine.provider_for_model(model_id)
            target_model = [{"id": model_id, "provider": provider_name}]
            print("Enter question for single-model test (press Enter for default):")
            q = input("> ").strip() or "Provide a structured analysis of local vs cloud vs hybrid AI architecture."
            test_session = store.create(f"Single Model Test: {model_id}", [model_id])
            try:
                engine.run(test_session, q, target_model, single_model_test=True, renderer=renderer)
            except Exception as exc:
                print(f"Single-model test error: {exc}")
            continue

        if cmd == "benchmark":
            if not arg:
                print("Usage: benchmark <file>")
                continue
            path = Path(arg)
            if not path.is_absolute():
                path = ROOT / path
            if not path.exists():
                print(f"Not found: {path}")
                continue
            if not selected:
                print("Select models first.")
                continue
            engine.run_benchmark(path, selected)
            continue


        # Question execution path (via 'ask <question>', 'ask' multi-line, or direct prompt paste)
        question = ""
        if cmd == "ask":
            if arg:
                question = arg
            else:
                print("Enter your question below. End with a blank line or type 'EOF' on a new line:")
                lines = []
                while True:
                    try:
                        line = input("> ")
                        if line.strip() == "EOF" or (not line.strip() and lines):
                            break
                        lines.append(line)
                    except (EOFError, KeyboardInterrupt):
                        break
                question = "\n".join(lines).strip()
        elif cmd not in KNOWN_COMMANDS:
            question = _read_buffered_stdin(raw)

        if question:
            if not selected:
                print("Select models first: select")
                continue
            if approved_role_plan is None:
                approved_role_plan = _prompt_role_plan(engine, renderer, selected)
                if approved_role_plan is None:
                    print("Role plan approval is required before asking a question.")
                    continue
            if not current_session:
                current_session = store.create("Interactive Session", [m["id"] for m in selected], role_plan=approved_role_plan)
                print(f"Created {current_session['session_id']}")
            try:
                current_session = engine.run(
                    current_session,
                    question,
                    selected,
                    role_plan=approved_role_plan,
                    renderer=renderer,
                )
            except InsufficientHealthyModels as exc:
                print(f"Council paused: {exc}")
            continue

        print("Unknown command. Type 'help'.")



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", choices=["models", "ask", "sessions", "resume", "history", "export", "debug"])
    parser.add_argument("argument", nargs="?")
    args = parser.parse_args()

    engine, store, renderer = build_engine()

    if not args.command:
        interactive(engine, store, renderer)
        return

    if args.command == "models":
        print_models(engine)
    elif args.command == "sessions":
        for s in store.list_sessions():
            print(f"{s['session_id']}  {s['title']}  {s['updated_at']}")
    elif args.command == "resume":
        s = store.load(args.argument or "")
        if s:
            selected = [{"id": x, "provider": engine.provider_for_model(x)} for x in s["models"]]
            print(renderer.render_status(s, selected, s.get("approved_role_plan") or s.get("role_plan")))
        else:
            print("Session not found.")
    elif args.command == "history":
        s = store.load(args.argument or "")
        if s:
            print_session_history(s)
        else:
            print("Session not found.")
    elif args.command in {"export", "debug"}:
        s = store.load(args.argument or "")
        print(json.dumps(s, indent=2, ensure_ascii=False) if s else "Session not found.")
    elif args.command == "ask":
        models = engine.discover_models()
        if not models:
            print("No Ollama models found.")
            return
        selected = models[:4]
        role_plan = engine.recommend_roles(selected)
        s = store.create("CLI Session", [m["id"] for m in selected], role_plan=role_plan)
        try:
            engine.run(s, args.argument or "", selected, role_plan=role_plan, renderer=renderer)
        except InsufficientHealthyModels as exc:
            print(f"Council paused: {exc}")


if __name__ == "__main__":
    main()
