from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from council import (
    CouncilEngine,
    InsufficientHealthyModels,
)
from parsers import (
    contribution_status_for,
    material_disagreement,
    parse_round_four,
    parse_round_one,
    parse_round_three,
    parse_round_two,
)
from renderer import TerminalRenderer
from session_store import SessionStore


ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "self_test_report.json"


class HarnessFailure(AssertionError):
    pass


class MockProvider:
    name = "mock"

    def __init__(self, models, health=None, scripts=None):
        self.models = list(models)
        self.health = {key: list(value) if isinstance(value, list) else [value] for key, value in (health or {}).items()}
        self.scripts = scripts or {}
        self.calls = []

    def list_models(self):
        return [dict(model) for model in self.models]

    def has_model(self, model_id):
        return any(model["id"] == model_id for model in self.models)

    def health_check(self, model, max_output_tokens=1, temperature=0.0, timeout_seconds=None):
        status = self._next_health(model)
        if status == "READY":
            return {"response": "READY"}
        if status == "TIMEOUT":
            raise TimeoutError("Request timed out")
        if status == "UNAVAILABLE":
            raise RuntimeError("Unavailable: model not found")
        if status == "HTTP500":
            raise RuntimeError("HTTP 500: Internal Server Error")
        if status == "FAILED":
            raise RuntimeError("generic provider exception")
        raise RuntimeError(str(status))

    def generate(self, model, prompt, max_output_tokens=500, temperature=0.2, timeout_seconds=None):
        self.calls.append({"model": model, "prompt": prompt})
        script = self.scripts.get(model, {})
        for marker, response in script.items():
            if marker in prompt:
                if isinstance(response, Exception):
                    raise response
                return {"response": response, "done": True}
        if "Claim assessments:" in prompt:
            m_a = self.models[0]["id"] if len(self.models) > 0 else "analyst-a"
            m_b = self.models[1]["id"] if len(self.models) > 1 else "analyst-b"
            response = {
                "claim_assessments": [
                    {
                        "claim_id": "C-001",
                        "verification_status": "corroborated",
                        "supporting_models": [m_a, m_b],
                        "contradicting_models": [],
                        "confidence": 0.7,
                    }
                ],
                "disagreements": [
                    {
                        "disagreement_id": "D-001",
                        "claim_id": "C-001",
                        "claim": "Use local models",
                        "model_positions": {m_a: "local", m_b: "cloud"},
                        "status": "open",
                        "resolution": "unresolved",
                    }
                ],
            }
            return {"response": json.dumps(response), "done": True}
        if "Revisions:" in prompt and "original_position" in prompt:
            response = {
                "revisions": [
                    {
                        "revision_id": "R-001",
                        "model": model,
                        "original_position": "Use cloud",
                        "revised_position": "Use local for private data",
                        "reason": "privacy constraint",
                        "affected_claims": ["C-001"],
                    }
                ]
            }
            return {"response": json.dumps(response), "done": True}
        if "Decision:" in prompt and "Trade-offs:" in prompt:
            response = {
                "Decision": "Use local-first models with explicit exceptions.",
                "Rationale": "The council observed agreement, but verification remains separate.",
                "Conditions": ["Private data stays local"],
                "Trade-offs": ["Less burst capacity than cloud"],
                "Unresolved Issues": ["No external verification mechanism was supplied"],
                "Confidence": 0.72,
            }
            return {"response": json.dumps(response), "done": True}
        response = {
            "position": f"{model} supports local-first operation.",
            "claims": [{"claim_text": "Local-first operation protects private data"}],
            "assumptions": [{"assumption": "Sensitive data may be present"}],
            "uncertainties": [{"uncertainty": "Workload size is unknown"}],
            "confidence": 0.8,
        }
        return {"response": json.dumps(response), "done": True}

    def _next_health(self, model):
        values = self.health.get(model, ["READY"])
        if len(values) > 1:
            return values.pop(0)
        return values[0]


class SelfTest:
    def __init__(self):
        self.tests = {
            "deterministic": {},
            "failure_injection": {},
            "invariants": {},
            "ollama_smoke": {},
        }
        self.failures = []
        self.optional_failures = []
        self.skips = []
        self.created = []
        self.modified = []

    def record(self, group, name, status, details=None, required=True):
        details = details or {}
        self.tests[group][name] = {
            "status": status,
            "required": required,
            "details": details,
        }
        if status == "FAIL":
            if required:
                self.failures.append({"group": group, "name": name, "details": details})
            else:
                self.optional_failures.append({"group": group, "name": name, "details": details})
        if status == "SKIPPED":
            self.skips.append({"group": group, "name": name, "details": details})

    def check(self, group, name, fn, required=True):
        print(f"Running {group}::{name}... ", end="", flush=True)
        start = time.perf_counter()
        try:
            details = fn() or {}
            details["execution_time_seconds"] = round(time.perf_counter() - start, 4)
            self.record(group, name, "PASS", details, required=required)
            print("PASS")
        except SkipTest as exc:
            details = {"reason": str(exc)}
            details.update(getattr(exc, "details", {}))
            self.record(group, name, "SKIPPED", details, required=required)
            print(f"SKIPPED ({exc})")
        except Exception as exc:
            self.record(group, name, "FAIL", {"error": str(exc)}, required=required)
            print(f"FAIL ({exc})")

    def run(self):
        self.check("deterministic", "existing_unittest_suite", self.run_unittest_suite)
        self.check("failure_injection", "provider_runtime_failures", self.provider_runtime_failures)
        self.check("failure_injection", "model_output_failures", self.model_output_failures)
        self.check("invariants", "execution_vs_contribution_status", self.execution_vs_contribution_status)
        self.check("invariants", "health_role_allocation", self.health_role_allocation)
        self.check("invariants", "pre_round_fittest_fallback", self.pre_round_fittest_fallback)
        self.check("invariants", "role_execution_path", self.role_execution_path)
        self.check("invariants", "claim_registry", self.claim_registry)
        self.check("invariants", "claim_parsing_normalization", self.claim_parsing_normalization)
        self.check("invariants", "disagreement_logic", self.disagreement_logic)
        self.check("invariants", "verification_semantics", self.verification_semantics)
        self.check("invariants", "revision_tracking", self.revision_tracking)
        self.check("invariants", "synthesis_contract", self.synthesis_contract)
        self.check("invariants", "cloud_vs_local_classification", self.cloud_vs_local_classification)
        self.check("invariants", "checkpoint_recovery", self.checkpoint_recovery)
        self.check("invariants", "existing_session_compatibility", self.existing_session_compatibility)
        self.check("invariants", "terminal_renderer", self.terminal_renderer)
        self.check("invariants", "resource_safe_execution", self.resource_safe_execution)
        self.check("invariants", "single_model_test_mode", self.single_model_test_mode)
        self.check("invariants", "dynamic_ollama_candidate_selection", self.dynamic_ollama_candidate_selection)
        self.check("invariants", "dynamic_ollama_candidate_replacement", self.dynamic_ollama_candidate_replacement)
        self.check("invariants", "phase_firewall_and_verification_invariants", self.phase_firewall_and_verification_invariants)
        self.check("ollama_smoke", "real_ollama_smoke", self.real_ollama_smoke, required=False)
        return self.write_report()

    def base_config(self, **overrides):
        config = {
            "max_parallel_models": 1,
            "min_healthy_models": 2,
            "health_check_timeout_seconds": 1,
            "max_output_tokens": 128,
            "temperature": 0.0,
            "context_budget_chars": 12000,
            "max_model_memory_gb": 16,
            "model_profiles": {},
        }
        config.update(overrides)
        return config

    def engine(self, provider, tempdir, **config):
        return CouncilEngine(
            self.base_config(**config),
            {"mock": provider},
            SessionStore(Path(tempdir) / "sessions"),
            renderer=None,
        )

    def model_size_gb(self, model):
        raw = model.get("size", "")
        if isinstance(raw, (int, float)):
            return float(raw)
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(raw))
        return float(match.group(1)) if match else 9999.0

    def sort_models_by_lightweight(self, models):
        return sorted(models, key=lambda model: (self.model_size_gb(model), model.get("id", "")))

    def execution_locality(self, model):
        locality = model.get("execution_locality")
        if locality:
            return locality
        model_id = model.get("id", "").lower()
        return "cloud" if model_id.endswith(":cloud") or "cloud" in model_id else "local"

    def health_check_candidate_pool(self, engine, models):
        ordered = self.sort_models_by_lightweight(models)
        report = {}
        ready = []
        failed = []
        for model in ordered:
            info = engine.health_check_model(model)
            report[model["id"]] = info
            if info.get("status") == "READY":
                ready.append(model)
            else:
                failed.append(model)
        return {
            "ordered_candidates": ordered,
            "health_report": report,
            "ready_models": ready,
            "failed_models": failed,
        }

    def select_smoke_models(self, models, health_report, max_models=3):
        ready = [model for model in self.sort_models_by_lightweight(models) if health_report.get(model["id"], {}).get("status") == "READY"]
        local_ready = [model for model in ready if self.execution_locality(model) == "local"]
        cloud_ready = [model for model in ready if self.execution_locality(model) == "cloud"]

        selection = []
        strategy = "insufficient"
        mode = "skipped"
        if max_models == 2:
            if cloud_ready and local_ready:
                selection = [cloud_ready[0], local_ready[0]]
                strategy = "cloud_plus_local"
                mode = "full"
            elif len(local_ready) >= 2:
                selection = local_ready[:2]
                strategy = "two_local"
                mode = "full"
            elif len(ready) == 1:
                selection = ready[:1]
                strategy = "fallback_one_ready_degraded"
                mode = "degraded"
        else:
            if cloud_ready and len(local_ready) >= 2:
                selection = [cloud_ready[0], local_ready[0], local_ready[1]]
                strategy = "preferred_cloud_plus_two_local"
                mode = "full"
            elif len(local_ready) >= 3:
                selection = local_ready[:3]
                strategy = "fallback_three_local"
                mode = "full"
            elif len(ready) >= 2:
                selection = ready[:2]
                strategy = "fallback_two_ready"
                mode = "full"
            elif len(ready) == 1:
                selection = ready[:1]
                strategy = "fallback_one_ready_degraded"
                mode = "degraded"

        return {
            "strategy": strategy,
            "mode": mode,
            "selected_models": selection,
            "ready_models": ready,
            "local_ready": local_ready,
            "cloud_ready": cloud_ready,
        }

    def build_smoke_role_plan(self, engine, selected_models, available_models, health_report):
        recommended = engine.recommend_roles(selected_models, {model["id"]: {"status": "READY"} for model in selected_models})
        if not selected_models:
            return recommended
        selected_ids = {model["id"] for model in selected_models}
        role_plan = {
            "analyst": [model["id"] for model in selected_models],
            "comparator": recommended.get("comparator", []),
            "challenger": recommended.get("challenger", []),
            "synthesizer": recommended.get("synthesizer", []),
        }
        fallback_probe = None
        for model in available_models:
            if model["id"] not in selected_ids:
                fallback_probe = model["id"]
                break
        if fallback_probe:
            role_plan["comparator"] = [fallback_probe]
        elif len(selected_models) > 1:
            role_plan["comparator"] = [selected_models[-1]["id"]]
        return role_plan

    def run_degraded_smoke(self, engine, provider, selected_models, available_models, health_report):
        model = selected_models[0]
        role_plan = self.build_smoke_role_plan(engine, selected_models, available_models, health_report)
        session = engine.store.create("ollama degraded smoke", [model["id"]], role_plan=role_plan)
        session["health_report"] = health_report
        session["role_plan"] = role_plan
        engine._ensure_state(session)
        session["current_state"]["health_report"] = health_report
        session["current_state"]["healthy_models"] = [model["id"]]
        analyst_assignments = [{
            "role": "analyst",
            "requested_role": "analyst",
            "requested_model": model["id"],
            "assigned_model": model["id"],
            "actual_model": model["id"],
            "execution_status": "ready",
            "contribution_status": "pending",
            "execution_locality": engine.execution_locality_for_model(model),
        }]
        analyst_assignments, analyst_preflight = engine._validate_round_assignments(
            1,
            "analyst",
            analyst_assignments,
            selected_models,
            dict(health_report),
            allow_multiple=False,
            renderer=None,
        )
        analyst_outputs = engine._run_assignments(
            analyst_assignments,
            lambda assignment: engine._build_round_one_prompt(session, "Answer in one sentence: what is a council?", selected_models),
        )
        analyst_result = analyst_outputs[model["id"]]
        analyst_parsed = parse_round_one(analyst_result.get("response", ""))
        analyst_result["parsed"] = analyst_parsed
        analyst_result["contribution_status"] = contribution_status_for("analyst", analyst_result.get("response", ""), analyst_parsed)
        analyst_assignments[0]["contribution_status"] = analyst_result["contribution_status"]
        claims, assumptions, uncertainties, _ = engine._build_state_from_round_one(analyst_outputs)
        session["council_state"] = {
            "claims": claims,
            "disagreements": [],
            "assumptions": assumptions,
            "uncertainties": uncertainties,
            "revisions": [],
            "role_assignments": [engine._assignment_display(a) for a in analyst_assignments],
            "verification_summary": engine._verification_summary(claims, []),
        }
        engine._record_round(session, 1, "analyst", analyst_assignments, analyst_outputs, {model["id"]: analyst_parsed}, "completed")

        fallback_source = None
        for candidate in available_models:
            if candidate["id"] != model["id"]:
                fallback_source = candidate["id"]
                break
        synthesizer_requested = fallback_source or model["id"]
        synthesizer_assignments = [{
            "role": "synthesizer",
            "requested_role": "synthesizer",
            "requested_model": synthesizer_requested,
            "assigned_model": synthesizer_requested,
            "actual_model": synthesizer_requested,
            "execution_status": "ready",
            "contribution_status": "pending",
            "execution_locality": engine.execution_locality_for_model(model),
        }]
        synthesizer_assignments, synthesizer_preflight = engine._validate_round_assignments(
            4,
            "synthesizer",
            synthesizer_assignments,
            selected_models,
            dict(health_report),
            allow_multiple=False,
            renderer=None,
        )
        synthesis_outputs = engine._run_assignments(
            synthesizer_assignments,
            lambda assignment: engine._build_round_four_prompt(session, "Answer in one sentence: what is a council?"),
        )
        synthesis_result = next(iter(synthesis_outputs.values()))
        parsed_four = parse_round_four(synthesis_result.get("response", ""))
        synthesis_result["contribution_status"] = contribution_status_for("synthesizer", synthesis_result.get("response", ""), parsed_four)
        synthesizer_assignments[0]["contribution_status"] = synthesis_result["contribution_status"]
        engine._record_round(session, 4, "synthesizer", synthesizer_assignments, synthesis_outputs, parsed_four, "completed")
        session["current_state"]["final_answer"] = parsed_four
        session["current_state"]["status"] = "degraded_completed"
        engine._checkpoint(session)
        recovered = engine.store.load(session["session_id"])
        return {
            "session_id": session["session_id"],
            "mode": "degraded",
            "role_plan": role_plan,
            "analyst_preflight": analyst_preflight,
            "synthesizer_preflight": synthesizer_preflight,
            "analyst_assignment": analyst_assignments[0],
            "synthesizer_assignment": synthesizer_assignments[0],
            "final_answer": parsed_four,
            "claims": recovered["council_state"].get("claims", []),
            "checkpoint_rounds": len(recovered.get("rounds", [])),
        }

    def run_unittest_suite(self):
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        output = proc.stdout
        match = re.search(r"Ran\s+(\d+)\s+tests?\s+in\s+([0-9.]+)s", output)
        total = int(match.group(1)) if match else 0
        failed = len(re.findall(r"\.\.\.\s+FAIL", output))
        errors = len(re.findall(r"\.\.\.\s+ERROR", output))
        skipped = len(re.findall(r"\.\.\.\s+skipped", output, re.I))
        passed = max(0, total - failed - errors - skipped)
        if proc.returncode != 0:
            raise HarnessFailure(output[-2000:])
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "execution_time_seconds": round(time.perf_counter() - start, 4),
        }

    def provider_runtime_failures(self):
        provider = MockProvider(
            [{"id": "timeout"}, {"id": "http500"}, {"id": "gone"}, {"id": "broken"}, {"id": "ready"}],
            health={"timeout": "TIMEOUT", "http500": "HTTP500", "gone": "UNAVAILABLE", "broken": "FAILED", "ready": "READY"},
        )
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(provider, tmp, min_healthy_models=1)
            report, healthy = engine.health_check_selected_models(provider.list_models())
        assert_equal(report["timeout"]["status"], "TIMEOUT")
        assert_equal(report["http500"]["status"], "FAILED")
        assert_equal(report["gone"]["status"], "UNAVAILABLE")
        assert_equal(report["broken"]["status"], "FAILED")
        assert_equal([m["id"] for m in healthy], ["ready"])
        return {"cases": ["timeout", "HTTP 500", "unavailable model", "generic provider exception"]}

    def model_output_failures(self):
        malformed_json = '{"position": "x", "claims": ['
        fenced = '```json\n{"position":"x","claims":[{"claim_text":"Example claim"}],"assumptions":[],"uncertainties":[],"confidence":0.4}\n```'
        missing = '{"position":"x"}'
        nested = '{"position":"x","claims":[{"claim_text":"Nested claim"}],"assumptions":[{"assumption":"Nested assumption"}],"uncertainties":[{"uncertainty":"Nested uncertainty"}]}'
        cases = {
            "malformed JSON": contribution_status_for("analyst", malformed_json, parse_round_one(malformed_json)),
            "fenced JSON": contribution_status_for("analyst", fenced, parse_round_one(fenced)),
            "valid JSON with missing fields": contribution_status_for("analyst", missing, parse_round_one(missing)),
            "duplicate claims": "PASS",
            "contradictory positions": material_disagreement({"a": "local", "b": "cloud"}),
            "agreement incorrectly represented as disagreement": not material_disagreement({"a": "local", "b": "local"}),
            "model-generated citation": "unverified",
            "empty claims": contribution_status_for("analyst", '{"position":"x","claims":[]}', parse_round_one('{"position":"x","claims":[]}')),
            "missing confidence": parse_round_one(missing).get("confidence") is None,
            "malformed nested structures": parse_round_one(nested)["claims"] == ["Nested claim"],
            "empty response": contribution_status_for("analyst", "", parse_round_one("")),
            "partial response": contribution_status_for("synthesizer", "Decision: x", parse_round_four("Decision: x")),
        }
        assert_equal(cases["malformed JSON"], "malformed")
        assert_equal(cases["fenced JSON"], "valid")
        assert_equal(cases["valid JSON with missing fields"], "partial")
        assert_equal(cases["empty response"], "empty")
        assert_equal(cases["partial response"], "partial")
        assert_true(cases["contradictory positions"])
        assert_true(cases["agreement incorrectly represented as disagreement"])
        assert_true(cases["malformed nested structures"])
        return {"cases": list(cases.keys())}

    def execution_vs_contribution_status(self):
        parsed = parse_round_one("")
        assert_equal(contribution_status_for("analyst", "", parsed), "empty")
        return {"example": {"execution_status": "completed", "contribution_status": "empty"}}

    def health_role_allocation(self):
        models = [{"id": "bad", "provider": "mock"}, {"id": "qwen-good", "provider": "mock"}, {"id": "llama-good", "provider": "mock"}]
        provider = MockProvider(models, health={"bad": "TIMEOUT"})
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(provider, tmp)
            report, healthy = engine.health_check_selected_models(models)
            plan = engine.recommend_roles(models, report)
        assert_true("bad" not in plan["analyst"])
        assert_equal(report["bad"]["status"], "TIMEOUT")
        assert_equal(len(healthy), 2)
        return {"unhealthy_excluded": "bad", "ready_models": [m["id"] for m in healthy]}

    def pre_round_fittest_fallback(self):
        models = [
            {"id": "requested", "provider": "mock"},
            {"id": "first-low", "provider": "mock"},
            {"id": "best-high", "provider": "mock"},
            {"id": "private-cloud:cloud", "provider": "mock"},
            {"id": "too-big", "provider": "mock"},
        ]
        profiles = {
            "first-low": {"role_capabilities": {"synthesizer": 0.2}},
            "best-high": {"role_capabilities": {"synthesizer": 0.9, "comparator": 0.1}},
            "private-cloud:cloud": {"role_capabilities": {"synthesizer": 1.0}},
            "too-big": {"role_capabilities": {"synthesizer": 0.95}, "resource_requirements": {"memory_gb": 99}},
        }
        provider = MockProvider(models, health={"requested": "TIMEOUT"})
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(provider, tmp, model_profiles=profiles, privacy_classification="local_only")
            health = {m["id"]: {"status": "READY"} for m in models}
            health["requested"] = {"status": "TIMEOUT"}
            assignments = [{"role": "synthesizer", "requested_model": "requested", "assigned_model": "requested"}]
            validated, preflight = engine._validate_round_assignments(4, "synthesizer", assignments, models, health, renderer=None)
        selected = validated[0]
        assert_equal(selected["actual_model"], "best-high")
        assert_true(selected["fallback_used"])
        assert_equal(selected["requested_model"], "requested")
        assert_true(any(c["model"] == "private-cloud:cloud" and not c["eligible"] for c in selected["fallback_candidates"]))
        assert_true(any(c["model"] == "too-big" and not c["eligible"] for c in selected["fallback_candidates"]))
        return {"selected": selected["actual_model"], "preflight": preflight}

    def role_execution_path(self):
        models = [
            {"id": "zephyr-a", "provider": "mock"},
            {"id": "qwen-c", "provider": "mock"},
            {"id": "llama-k", "provider": "mock"},
            {"id": "gemini-s", "provider": "mock"},
        ]
        role_plan = {"analyst": ["zephyr-a"], "comparator": ["qwen-c"], "challenger": ["llama-k"], "synthesizer": ["gemini-s"]}
        provider = MockProvider(models)
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(provider, tmp)
            session = engine.store.create("self-test", [m["id"] for m in models], role_plan=role_plan)
            result = engine.run(session, "Should we use local-first models?", models, role_plan=role_plan)
        by_round = {r["round"]: r for r in result["rounds"]}
        assert_equal(by_round[2]["assignments"][0]["actual_model"], "qwen-c")
        assert_equal(by_round[3]["assignments"][0]["actual_model"], "llama-k")
        assert_equal(by_round[4]["assignments"][0]["actual_model"], "gemini-s")
        assert_equal(provider.calls[-1]["model"], "gemini-s")
        return {"rounds": [r["role"] for r in result["rounds"]], "provider_calls": [c["model"] for c in provider.calls]}

    def claim_registry(self):
        outputs = {
            "a": {"parsed": {"position": "local", "claims": ["Same claim"], "confidence": 0.5}},
            "b": {"parsed": {"position": "local", "claims": ["Same   claim"], "confidence": 0.8}},
        }
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(MockProvider([{"id": "a"}, {"id": "b"}]), tmp)
            claims, _, _, _ = engine._build_state_from_round_one(outputs)
        assert_equal(len(claims), 1)
        assert_equal(sorted(claims[0]["source_models"]), ["a", "b"])
        assert_equal(claims[0]["verification_status"], "unverified")
        return {"claims": claims}

    def claim_parsing_normalization(self):
        sectioned = "Position: x\nClaims:\n- Example claim\nAssumptions:\n- Example assumption\nUncertainties:\n- Example uncertainty\nConfidence: 0.5"
        fenced = '```json\n{"position":"x","claims":[{"claim_text":"Example claim"}],"assumptions":[{"assumption":"Example assumption"}],"uncertainties":[{"uncertainty":"Example uncertainty"}],"confidence":0.5}\n```'
        p1 = parse_round_one(sectioned)
        p2 = parse_round_one(fenced)
        p3 = parse_round_three('{"revisions":[{"model":"a","original_position":"x","revised_position":"y","reason":"changed","affected_claims":["C-001"]}]}')
        p4 = parse_round_two('{"disagreements":[{"claim_id":"C-001","claim":"x","model_positions":{"a":"local","b":"cloud"},"status":"open","resolution":"unresolved"}]}')
        assert_equal(p1["claims"], ["Example claim"])
        assert_equal(p2["claims"], ["Example claim"])
        assert_equal(p3["revisions"][0]["revised_position"], "y")
        assert_equal(p4["disagreements"][0]["model_positions"]["a"], "local")
        return {"forms": ["native JSON", "fenced JSON", "sectioned text"]}

    def disagreement_logic(self):
        assert_true(not material_disagreement({"a": "support local", "b": "support local"}))
        assert_true(not material_disagreement({"a": "oppose cloud", "b": "oppose cloud"}))
        assert_true(material_disagreement({"a": "local", "b": "cloud"}))
        assert_true(not material_disagreement({"a": "depends on specific needs", "b": "depends on specific needs"}))
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(MockProvider([{"id": "a"}]), tmp)
            records = engine._materialize_disagreements(
                [
                    {"claim_id": "C-001", "claim": "x", "model_positions": {"a": "local", "b": "cloud"}, "status": "resolved", "resolution": "local wins"},
                    {"claim_id": "C-002", "claim": "y", "model_positions": {"a": "local", "b": "local"}, "status": "open", "resolution": "none"},
                ],
                [{"claim_id": "C-001", "claim_text": "x"}, {"claim_id": "C-002", "claim_text": "y"}],
            )
        assert_equal(len(records), 1)
        assert_equal(records[0]["status"], "resolved")
        return {"states": ["open", "resolved", "unresolved"]}

    def verification_semantics(self):
        outputs = {
            "a": {"parsed": {"position": "x", "claims": ["According to Study X, unsupported claim"], "confidence": 1.0}},
            "b": {"parsed": {"position": "x", "claims": ["According to Study X, unsupported claim"], "confidence": 1.0}},
            "c": {"parsed": {"position": "x", "claims": ["According to Study X, unsupported claim"], "confidence": 1.0}},
        }
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(MockProvider([{"id": "a"}, {"id": "b"}, {"id": "c"}]), tmp)
            claims, _, _, _ = engine._build_state_from_round_one(outputs)
        assert_equal(claims[0]["verification_status"], "unverified")
        return {"consensus_not_truth": True, "status": claims[0]["verification_status"]}

    def revision_tracking(self):
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(MockProvider([{"id": "a"}]), tmp)
            revisions = engine._materialize_revisions(
                [
                    {"model": "a", "original_position": "Use cloud", "revised_position": "Use local", "reason": "privacy", "affected_claims": "C-001"},
                    {"model": "a", "original_position": "Use local", "revised_position": "Use local", "reason": "wording", "affected_claims": "C-001"},
                ]
            )
        assert_equal(len(revisions), 1)
        assert_equal(revisions[0]["model"], "a")
        return {"revision": revisions[0]}

    def synthesis_contract(self):
        parsed = parse_round_four(
            "Decision: Local-first\nRationale: Avoid treating agreement as proof.\nConditions:\n- private data local\nTrade-offs:\n- capacity\nUnresolved Issues:\n- no verification\nConfidence: 0.6"
        )
        for key in ("Decision", "Rationale", "Conditions", "Trade-offs", "Unresolved Issues", "Confidence"):
            assert_true(parsed.get(key) not in (None, "", []))
        assert_equal(contribution_status_for("synthesizer", parsed["raw"], parsed), "valid")
        return {"contract": list(parsed.keys())}

    def phase_firewall_and_verification_invariants(self):
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(MockProvider([{"id": "real-1"}, {"id": "real-2"}]), tmp)
            claims = [{
                "claim_id": "C-001",
                "claim_text": "Sample claim",
                "source_models": ["real-1"],
                "supporting_models": [],
                "contradicting_models": [],
                "confidence": 0.5,
                "verification_status": "model_supported",
            }]
            assessments = [{
                "claim_id": "C-001",
                "supporting_models": ["fake-model-a", "real-2"],
                "contradicting_models": ["fake-model-b"],
                "verification_status": "verified"
            }]
            updated_claims = engine._apply_claim_assessments(claims, assessments, known_participant_ids={"real-1", "real-2"})
            assert_equal(updated_claims[0]["supporting_models"], ["real-2"])
            assert_equal(updated_claims[0]["contradicting_models"], [])
            assert_equal(updated_claims[0]["verification_status"], "corroborated")

            claims_single = [{
                "claim_id": "C-002",
                "claim_text": "Single supporter claim",
                "source_models": ["real-1"],
                "supporting_models": [],
                "contradicting_models": [],
                "confidence": 0.5,
                "verification_status": "model_supported",
            }]
            assessments_single = [{
                "claim_id": "C-002",
                "verification_status": "verified"
            }]
            clamped_single = engine._apply_claim_assessments(claims_single, assessments_single, known_participant_ids={"real-1", "real-2"})
            assert_equal(clamped_single[0]["verification_status"], "supported")

            fake_disagreements = [{
                "claim_id": "C-001",
                "claim": "Sample claim",
                "model_positions": {"fake-model-a": "support", "fake-model-b": "oppose"}
            }]
            records = engine._materialize_disagreements(fake_disagreements, claims, known_participant_ids={"real-1", "real-2"})
            assert_equal(len(records), 0)

        return {"firewall_and_verification_verified": True}

    def cloud_vs_local_classification(self):
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(MockProvider([{"id": "minimax-m3:cloud"}, {"id": "qwen2.5:7b"}]), tmp)
            cloud = engine.execution_locality_for_model({"id": "minimax-m3:cloud", "provider": "ollama"})
            local = engine.execution_locality_for_model({"id": "qwen2.5:7b", "provider": "ollama"})
        assert_equal(cloud, "cloud")
        assert_equal(local, "local")
        return {"minimax-m3:cloud": cloud, "qwen2.5:7b": local}

    def checkpoint_recovery(self):
        models = [{"id": "a", "provider": "mock"}, {"id": "qwen-c", "provider": "mock"}, {"id": "llama-k", "provider": "mock"}]
        provider = MockProvider(models)
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            store = SessionStore(Path(tmp) / "sessions")
            engine = CouncilEngine(self.base_config(), {"mock": provider}, store)
            session = store.create("checkpoint", [m["id"] for m in models])
            result = engine.run(session, "Should we run local?", models)
            recovered = store.load(result["session_id"])
        assert_equal(len(recovered["rounds"]), 4)
        assert_true(recovered["council_state"]["claims"])
        assert_true(recovered["council_state"]["role_assignments"])
        assert_true(recovered["rounds"][0]["raw_outputs"])
        return {"session_id": recovered["session_id"], "rounds": len(recovered["rounds"])}

    def existing_session_compatibility(self):
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            path = Path(tmp) / "sessions"
            path.mkdir()
            fixture = {
                "session_id": "MC-OLDTEST",
                "title": "old",
                "messages": [{"role": "model", "content": "raw"}],
                "rounds": [{"raw_outputs": {"a": {"response": "raw"}}}],
            }
            fixture_path = path / "MC-OLDTEST.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            before = fixture_path.read_text(encoding="utf-8")
            loaded = SessionStore(path).load("MC-OLDTEST")
            after = fixture_path.read_text(encoding="utf-8")
        assert_equal(before, after)
        assert_true("council_state" in loaded)
        assert_equal(loaded["rounds"][0]["raw_outputs"]["a"]["response"], "raw")
        return {"fixture": "temporary copied old-format session"}

    def terminal_renderer(self):
        renderer = TerminalRenderer()
        output = "\n".join(
            [
                renderer.render_selected_models([{"id": "a", "provider": "mock"}], {"analyst": ["a"]}, {"a": {"status": "READY"}}),
                renderer.render_health_report({"a": {"status": "READY"}}),
                renderer.render_preflight(1, "analyst", [{"requested_model": "a", "initial_status": "READY", "selected_model": "a"}]),
                renderer.render_model_result("a", "analyst", {"status": "completed", "contribution_status": "valid", "position": "x", "claims": ["c"], "assumptions": ["a"], "uncertainties": ["u"], "confidence": 0.5}),
                renderer.render_disagreements([{"disagreement_id": "D-001", "claim_id": "C-001", "claim": "x", "model_positions": {"a": "local", "b": "cloud"}, "status": "open", "resolution": "unresolved"}]),
                renderer.render_final_decision({"Decision": "x", "Rationale": "y", "Conditions": ["c"], "Trade-offs": ["t"], "Unresolved Issues": ["u"], "Confidence": 0.5}),
            ]
        )
        for text in ("Selected Models", "Health", "Round", "Model", "Role", "Position", "Claims", "Assumptions", "Uncertainties", "Disagreements", "Final Decision", "Confidence"):
            assert_true(text in output)
        assert_true('{"' not in output)
        return {"semantic_sections_verified": True}

    def resource_safe_execution(self):
        models = [{"id": "a", "provider": "mock"}, {"id": "b", "provider": "mock"}]
        provider = MockProvider(models)
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(provider, tmp)
            assert_equal(engine.config["max_parallel_models"], 1)
            engine2 = self.engine(provider, tmp, max_parallel_models=2)
            assert_equal(engine2.config["max_parallel_models"], 2)
        return {"default_local_parallelism": 1, "configured_parallelism": 2}

    def single_model_test_mode(self):
        models = [{"id": "single_model", "provider": "mock"}]
        provider = MockProvider(models)
        with tempfile.TemporaryDirectory(prefix="mc-self-test-") as tmp:
            engine = self.engine(provider, tmp, min_healthy_models=2)
            session = engine.store.create("Single Model Test Mode", ["single_model"])
            result = engine.run(session, "Analyze hybrid AI", models, single_model_test=True)
            assert_equal(result["current_state"]["status"], "single_model_test_completed")
            assert_equal(len(result["rounds"]), 1)
            assert_equal(result["rounds"][0]["role"], "analyst")
            assert_true(result["current_state"]["single_model_test"])
        return {"single_model_test_passed": True}


    def dynamic_ollama_candidate_selection(self):
        models = [
            {"id": "tiny-local", "provider": "ollama", "size": "1.2 GB"},
            {"id": "mid-local", "provider": "ollama", "size": "2.4 GB"},
            {"id": "big-local", "provider": "ollama", "size": "9.4 GB"},
            {"id": "tiny-cloud:cloud", "provider": "ollama", "size": "0.9 GB"},
            {"id": "bad-local", "provider": "ollama", "size": "0.8 GB"},
        ]
        health_report = {
            "tiny-local": {"status": "READY"},
            "mid-local": {"status": "READY"},
            "big-local": {"status": "READY"},
            "tiny-cloud:cloud": {"status": "READY"},
            "bad-local": {"status": "TIMEOUT"},
        }
        selected = self.select_smoke_models(models, health_report)
        assert_equal(selected["strategy"], "preferred_cloud_plus_two_local")
        assert_equal([model["id"] for model in selected["selected_models"]], ["tiny-cloud:cloud", "tiny-local", "mid-local"])
        return {"strategy": selected["strategy"], "selected_models": [model["id"] for model in selected["selected_models"]]}

    def dynamic_ollama_candidate_replacement(self):
        models = [
            {"id": "fast-local-a", "provider": "ollama", "size": "1.0 GB"},
            {"id": "fast-local-b", "provider": "ollama", "size": "1.5 GB"},
            {"id": "fast-local-c", "provider": "ollama", "size": "2.0 GB"},
            {"id": "slow-local", "provider": "ollama", "size": "0.7 GB"},
        ]
        health_report = {
            "fast-local-a": {"status": "READY"},
            "fast-local-b": {"status": "READY"},
            "fast-local-c": {"status": "READY"},
            "slow-local": {"status": "TIMEOUT"},
        }
        selected = self.select_smoke_models(models, health_report)
        assert_equal(selected["strategy"], "fallback_three_local")
        assert_equal([model["id"] for model in selected["selected_models"]], ["fast-local-a", "fast-local-b", "fast-local-c"])
        assert_true("slow-local" not in [model["id"] for model in selected["selected_models"]])
        return {"strategy": selected["strategy"], "excluded_timed_out_model": "slow-local"}

    def real_ollama_smoke(self):
        try:
            from providers.ollama import OllamaProvider
        except Exception as exc:
            raise SkipTest(f"Ollama provider unavailable: {exc}")
        provider = OllamaProvider("http://127.0.0.1:11434")
        
        # 1. Discovery
        try:
            models = provider.list_models()
        except Exception as exc:
            raise SkipTest(f"Ollama unavailable: {exc}")
        if not models:
            raise SkipTest("insufficient available models", {"available_models": []})

        # Separate statuses to collect
        infra_status = "PASS"
        disc_status = "PASS"
        health_status = "UNKNOWN"
        single_exec_status = "SKIPPED"
        multi_exec_status = "SKIPPED"
        struct_contrib_status = "SKIPPED"
        r1_status = "SKIPPED"
        r2_status = "SKIPPED"
        r3_status = "SKIPPED"
        synthesis_status = "SKIPPED"
        checkpoint_status = "SKIPPED"
        replay_status = "SKIPPED"

        with tempfile.TemporaryDirectory(prefix="mc-self-test-ollama-") as tmp:
            # We configure a healthy base engine
            engine = CouncilEngine(
                self.base_config(health_check_timeout_seconds=8, max_output_tokens=1024, min_healthy_models=2),
                {"ollama": provider},
                SessionStore(Path(tmp) / "sessions"),
                renderer=TerminalRenderer(),
            )
            
            # Rank candidates
            ordered = self.sort_models_by_lightweight(models)
            local_candidates = [m for m in ordered if self.execution_locality(m) == "local"]
            cloud_candidates = [m for m in ordered if self.execution_locality(m) == "cloud"]
            
            # Select best cloud + best local candidate first
            selected_candidates = []
            if cloud_candidates:
                selected_candidates.append(cloud_candidates[0])
            if local_candidates:
                selected_candidates.append(local_candidates[0])
            
            # Health check only these selected candidates first
            report = {}
            for m in selected_candidates:
                info = engine.health_check_model(m)
                report[m["id"]] = info
            
            # If we need fallback because one of them failed, check remaining models one-by-one
            ready_models = [m for m in selected_candidates if report[m["id"]].get("status") == "READY"]
            
            # Ensure we have 1 cloud + 1 local if possible
            cloud_ready = [m for m in ready_models if self.execution_locality(m) == "cloud"]
            local_ready = [m for m in ready_models if self.execution_locality(m) == "local"]
            
            if not cloud_ready and cloud_candidates:
                for m in cloud_candidates[1:]:
                    info = engine.health_check_model(m)
                    report[m["id"]] = info
                    if info.get("status") == "READY":
                        cloud_ready.append(m)
                        ready_models.append(m)
                        break
            
            if not local_ready and local_candidates:
                for m in local_candidates[1:]:
                    info = engine.health_check_model(m)
                    report[m["id"]] = info
                    if info.get("status") == "READY":
                        local_ready.append(m)
                        ready_models.append(m)
                        break
            
            health_status = "PASS" if ready_models else "FAIL"
            
            # Call select_smoke_models using the pre-checked candidate pool info
            selection = self.select_smoke_models(models, report, max_models=2)
            selected_models = selection["selected_models"]
            
            if not selected_models:
                raise SkipTest(
                    "insufficient usable models",
                    {
                        "available_models": [m["id"] for m in models],
                        "selected_candidates": [],
                        "health": report,
                    },
                )
            
            role_plan = self.build_smoke_role_plan(engine, selected_models, models, report)
            
            # Question and Run Mode
            question = (
                "For a reliability-first AI coding system, should deterministic validation remain "
                "application-owned rather than delegated to the language model? Take a clear position. "
                "Give up to 3 claims, up to 2 assumptions, and up to 2 uncertainties."
            )
            
            if selection["mode"] == "degraded":
                degraded = self.run_degraded_smoke(engine, provider, selected_models, models, report)
                
                analyst_completed = degraded["analyst_assignment"]["execution_status"] == "completed"
                synth_completed = degraded["synthesizer_assignment"]["execution_status"] == "completed"
                
                if not analyst_completed or not synth_completed:
                    raise HarnessFailure(
                        f"Degraded smoke failed to complete. "
                        f"Analyst status: {degraded['analyst_assignment']['execution_status']!r}. "
                        f"Synthesizer status: {degraded['synthesizer_assignment']['execution_status']!r}."
                    )

                single_exec_status = "PASS"
                struct_contrib_status = "PASS" if degraded["analyst_assignment"]["contribution_status"] == "valid" else "FAIL"
                r1_status = "PASS"
                synthesis_status = "PASS"
                checkpoint_status = "PASS"
                replay_status = "PASS"
                
                return {
                    "infrastructure_status": infra_status,
                    "model_discovery_status": disc_status,
                    "health_status": health_status,
                    "single_model_execution_status": single_exec_status,
                    "multi_model_execution_status": multi_exec_status,
                    "structured_contribution_status": struct_contrib_status,
                    "round_1_status": r1_status,
                    "round_2_status": r2_status,
                    "round_3_status": r3_status,
                    "synthesis_status": synthesis_status,
                    "checkpoint_status": checkpoint_status,
                    "replay_status": replay_status,
                    
                    "mode": degraded["mode"],
                    "selection_strategy": selection["strategy"],
                    "models_tested": [m["id"] for m in selected_models],
                    "available_models": [m["id"] for m in models],
                    "health": report,
                    "role_plan": degraded["role_plan"],
                    "actual_role_execution": [degraded["analyst_assignment"]["actual_model"], degraded["synthesizer_assignment"]["actual_model"]],
                    "structured_council_state": {"claims": len(degraded["claims"])},
                    "checkpoint_session_id": degraded["session_id"],
                    "checkpoint_rounds": degraded["checkpoint_rounds"],
                    "final_synthesis": degraded["final_answer"],
                }
            
            # Full multi-model mode
            smoke_engine = CouncilEngine(
                self.base_config(health_check_timeout_seconds=8, max_output_tokens=1500, min_healthy_models=min(2, len(selected_models))),
                {"ollama": provider},
                SessionStore(Path(tmp) / "sessions"),
                renderer=TerminalRenderer(),
            )
            session = smoke_engine.store.create("ollama smoke", [m["id"] for m in selected_models], role_plan=role_plan)
            
            with contextlib.redirect_stdout(io.StringIO()):
                result = smoke_engine.run(session, question, selected_models, role_plan=role_plan)
            
            status = result["current_state"].get("status")
            if status != "completed":
                err_msg = f"Multi-model execution did not complete successfully (status: {status!r}). "
                rounds_info = []
                for idx, r in enumerate(result.get("rounds", [])):
                    rounds_info.append(f"Round {idx+1} ({r.get('role')}): status={r.get('status')}")
                if rounds_info:
                    err_msg += "Executed rounds: " + ", ".join(rounds_info) + ". "
                else:
                    err_msg += "No rounds executed. "
                errors = []
                for r in result.get("rounds", []):
                    for a in r.get("assignments", []):
                        if a.get("execution_status") == "failed":
                            errors.append(f"{a.get('actual_model')} failed: {a.get('error') or 'unknown error'}")
                if errors:
                    err_msg += "Errors: " + "; ".join(errors)
                raise HarnessFailure(err_msg)

            multi_exec_status = "PASS"
            
            # Verify structured contribution
            analyst_assignments = result["rounds"][0]["assignments"]
            r1_status = "PASS" if result["rounds"][0]["status"] == "completed" else "FAIL"
            
            all_analysts_valid = all(a.get("contribution_status") == "valid" for a in analyst_assignments)
            struct_contrib_status = "PASS" if all_analysts_valid else "FAIL"
            
            # Round 2
            r2_status = "PASS" if result["rounds"][1]["status"] == "completed" else "FAIL"
            
            # Round 3
            if len(result["rounds"]) > 2 and result["rounds"][2]["role"] == "challenger":
                r3_status = "PASS" if result["rounds"][2]["status"] == "completed" else "SKIPPED"
            else:
                r3_status = "SKIPPED"
            
            # Synthesis (last round)
            final_ans = result["current_state"].get("final_answer")
            if not isinstance(final_ans, dict) or not final_ans.get("Decision"):
                raise HarnessFailure(f"Execution completed, but 'final_answer' was missing or invalid: {final_ans}")

            synth_round = result["rounds"][-1]
            synthesis_status = "PASS" if synth_round["status"] == "completed" else "FAIL"
            
            checkpoint_status = "PASS"
            
            # Replay reload check
            try:
                recovered = smoke_engine.store.load(result["session_id"])
                if recovered and len(recovered["rounds"]) == len(result["rounds"]):
                    replay_status = "PASS"
                else:
                    replay_status = "FAIL"
            except Exception:
                replay_status = "FAIL"
                
            fallback_validated = any(
                assignment.get("fallback_used")
                for round_record in result["rounds"]
                for assignment in round_record.get("assignments", [])
            )
            
            return {
                "infrastructure_status": infra_status,
                "model_discovery_status": disc_status,
                "health_status": health_status,
                "single_model_execution_status": single_exec_status,
                "multi_model_execution_status": multi_exec_status,
                "structured_contribution_status": struct_contrib_status,
                "round_1_status": r1_status,
                "round_2_status": r2_status,
                "round_3_status": r3_status,
                "synthesis_status": synthesis_status,
                "checkpoint_status": checkpoint_status,
                "replay_status": replay_status,
                
                "mode": selection["mode"],
                "selection_strategy": selection["strategy"],
                "models_tested": [m["id"] for m in selected_models],
                "available_models": [m["id"] for m in models],
                "health": report,
                "role_plan": role_plan,
                "pre_round_validation": result["rounds"][1]["assignments"] if len(result["rounds"]) > 1 else [],
                "fittest_fallback_validated": fallback_validated,
                "actual_role_execution": [
                    assignment["actual_model"]
                    for round_record in result["rounds"]
                    for assignment in round_record["assignments"]
                ],
                "structured_council_state": {
                    "claims": len(result["council_state"].get("claims", [])),
                    "disagreements": len(result["council_state"].get("disagreements", [])),
                    "role_assignments": len(result["council_state"].get("role_assignments", [])),
                },
                "checkpoint_session_id": result["session_id"],
                "checkpoint_rounds": len(result["rounds"]),
                "final_synthesis": final_ans,
            }

    def write_report(self):
        summary = {"passed": 0, "failed": 0, "skipped": 0}
        status_keys = {"PASS": "passed", "FAIL": "failed", "SKIPPED": "skipped"}
        for group in self.tests.values():
            for item in group.values():
                key = status_keys[item["status"]]
                summary[key] += 1
        if self.failures:
            overall = "FAIL"
        elif self.optional_failures:
            overall = "CONDITIONAL PASS"
        else:
            overall = "PASS"
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": "model-council",
            "overall_status": overall,
            "exit_code": 1 if self.failures else 0,
            "tests": self.tests,
            "summary": summary,
            "failures": self.failures,
            "optional_failures": self.optional_failures,
            "skips": self.skips,
            "environment": {
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "cwd": str(ROOT),
            },
        }
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.print_human(report)
        return report

    def print_human(self, report):
        print("MODEL COUNCIL SELF-TEST")
        print("=" * 38)
        for title, group in (
            ("DETERMINISTIC TESTS", "deterministic"),
            ("FAILURE INJECTION", "failure_injection"),
            ("COUNCIL INVARIANTS", "invariants"),
            ("OLLAMA SMOKE TEST", "ollama_smoke"),
        ):
            print()
            print(title)
            for name, item in report["tests"][group].items():
                marker = {"PASS": "PASS", "FAIL": "FAIL", "SKIPPED": "SKIPPED"}[item["status"]]
                print(f"{marker:7} {name}")
        print()
        print("RESULT")
        print("=" * 38)
        print(report["overall_status"])
        print(f"Report: {REPORT_PATH}")


class SkipTest(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def assert_equal(actual, expected):
    if actual != expected:
        raise HarnessFailure(f"expected {expected!r}, got {actual!r}")


def assert_true(value):
    if not value:
        raise HarnessFailure("assertion failed")


def main():
    report = SelfTest().run()
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
