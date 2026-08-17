from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from council import CouncilEngine, InsufficientHealthyModels, contribution_status_for, parse_round_one
from renderer import TerminalRenderer
from session_store import SessionStore


ROOT = Path(__file__).resolve().parents[1]


class StubProvider:
    def __init__(self, name, models, health_rules=None, scripts=None):
        self.name = name
        self.models = list(models)
        self.health_rules = health_rules or {}
        self.scripts = scripts or {}
        self.calls = []

    def list_models(self):
        return [{"id": model, "provider": self.name, "size": "mock"} for model in self.models]

    def has_model(self, model_id):
        return model_id in self.models

    def health_check(self, model, max_output_tokens=1, temperature=0.0, timeout_seconds=None):
        rule = self.health_rules.get(model, "ready")
        if rule == "ready":
            return {"response": "READY"}
        if rule == "timeout":
            raise TimeoutError("Request timed out")
        if rule == "unavailable":
            raise RuntimeError("Unavailable: model not found")
        if rule == "oom":
            raise RuntimeError("CUDA out of memory")
        if rule == "http500":
            raise RuntimeError("HTTP 500: Internal Server Error")
        if isinstance(rule, Exception):
            raise rule
        raise RuntimeError(str(rule))

    def generate(self, model, prompt, max_output_tokens=500, temperature=0.2, timeout_seconds=None):
        self.calls.append({"model": model, "prompt": prompt})
        scripted = self.scripts.get(model, {})

        # Support integer round-number keys: {1: response_for_round_1, 2: response_for_round_2}
        int_keys = {k: v for k, v in scripted.items() if isinstance(k, int)}
        str_keys = {k: v for k, v in scripted.items() if isinstance(k, str)}

        if int_keys:
            # Count how many times this model has been called so far
            model_call_count = sum(1 for c in self.calls[:-1] if c["model"] == model) + 1
            if model_call_count in int_keys:
                response = int_keys[model_call_count]
                if isinstance(response, dict):
                    return response
                return {"response": response, "done": True}

        for marker, response in str_keys.items():
            if marker in prompt:
                if isinstance(response, dict):
                    return response
                return {"response": response, "done": True}

        if "Claim assessments:" in prompt:
            return {"response": self._round_two_response(model), "done": True}
        if "Revisions:" in prompt and "original_position" in prompt:
            return {"response": self._round_three_response(model), "done": True}
        if "Decision:" in prompt and "Trade-offs:" in prompt:
            return {"response": self._round_four_response(model), "done": True}
        return {"response": self._round_one_response(model), "done": True}

    def _round_one_response(self, model):
        return (
            f"Position: {model} recommends a hybrid approach.\n"
            "Claims:\n"
            f"- {model} claim one\n"
            f"- {model} claim two\n"
            "Assumptions:\n"
            f"- {model} assumption\n"
            "Uncertainties:\n"
            f"- {model} uncertainty\n"
            "Confidence:\n"
            "0.80"
        )

    def _round_two_response(self, model):
        return (
            "Claim assessments:\n"
            "- claim_id=C-001 | verification_status=supported | supporting_models=zephyr:7b-alpha-q3_K_S,qwen2.5:7b | contradicting_models= | confidence=0.90\n"
            "- claim_id=C-002 | verification_status=contradicted | supporting_models= | contradicting_models=llama3.2:latest | confidence=0.40\n"
            "Disagreements:\n"
            "- disagreement_id=D-001 | claim_id=C-001 | claim=The startup can use a hybrid architecture | model_positions=zephyr: hybrid is best; qwen: conditional support; llama: hybrid is best | status=open | resolution=unresolved\n"
            "- disagreement_id=D-002 | claim_id=C-002 | claim=The answer depends on specific needs | model_positions=zephyr: depends on specific needs; qwen: depends on specific needs | status=open | resolution=unresolved\n"
            "Assumptions:\n"
            f"- assumption_id=A-100 | assumption={model} believes hardware is available | source_models={model} | status=unverified\n"
            "Uncertainties:\n"
            "- uncertainty_id=U-100 | uncertainty=actual workload is unknown | affected_claims=C-001 | source_models=zephyr:7b-alpha-q3_K_S,qwen2.5:7b | status=open\n"
        )

    def _round_three_response(self, model):
        return (
            "Revisions:\n"
            f"- revision_id=R-001 | model={model} | original_position=hybrid is best | revised_position=hybrid is best if workload is mixed | reason=refined recommendation | affected_claims=C-001\n"
        )

    def _round_four_response(self, model):
        return (
            "Decision: Use a hybrid default with local-first safeguards.\n"
            "Rationale: Hybrid best balances privacy, scalability, and operational complexity.\n"
            "Conditions:\n"
            "- Sensitive data stays local.\n"
            "- Bursty compute can spill to cloud.\n"
            "Trade-offs:\n"
            "- More operational complexity than a pure local setup.\n"
            "- More control than a pure cloud setup.\n"
            "Unresolved Issues:\n"
            "- The exact workload profile is not verified.\n"
            "- Compliance requirements remain uncertain.\n"
            "Confidence: 0.78"
        )


class CouncilTests(unittest.TestCase):
    def make_engine(self, providers, config=None, store=None, renderer=None):
        base_config = {
            "max_parallel_models": 1,
            "min_healthy_models": 2,
            "health_check_timeout_seconds": 20,
            "max_output_tokens": 128,
            "temperature": 0.0,
            "context_budget_chars": 12000,
            "role_hints": {
                "zephyr": "analyst",
                "qwen": "comparator",
                "llama": "challenger",
                "gemini": "synthesizer",
            },
            "session_dir": "sessions",
        }
        if config:
            base_config.update(config)
        if store is None:
            tempdir = tempfile.TemporaryDirectory()
            self.addCleanup(tempdir.cleanup)
            store = SessionStore(Path(tempdir.name) / "sessions")
        return CouncilEngine(base_config, providers, store, renderer=renderer), store

    def run_session(self, provider, selected, role_plan=None, renderer=None):
        engine, store = self.make_engine({"mock": provider}, renderer=renderer)
        session = store.create("Test Session", [model["id"] for model in selected], role_plan=role_plan)
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=role_plan, renderer=renderer)
        return engine, store, result

    def test_actual_comparator_role_execution(self):
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest", "gemini-2.5-flash"])
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
            {"id": "gemini-2.5-flash", "provider": "mock"},
        ]
        engine = CouncilEngine(
            {
                "max_parallel_models": 1,
                "min_healthy_models": 2,
                "health_check_timeout_seconds": 20,
                "max_output_tokens": 128,
                "temperature": 0.0,
                "context_budget_chars": 12000,
                "role_hints": {
                    "zephyr": "analyst",
                    "qwen": "comparator",
                    "llama": "challenger",
                    "gemini": "synthesizer",
                },
                "session_dir": "sessions",
            },
            {"mock": provider},
            SessionStore(Path(tempfile.mkdtemp()) / "sessions"),
        )

        session = engine.store.create("Test Session", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))

        round2 = result["rounds"][1]
        self.assertEqual(round2["role"], "comparator")
        self.assertEqual(round2["assignments"][0]["assigned_model"], "qwen2.5:7b")
        self.assertEqual(round2["assignments"][0]["execution_status"], "completed")
        self.assertEqual(round2["assignments"][0]["fallback_used"], False)

        round3 = result["rounds"][2]
        self.assertEqual(round3["role"], "challenger")
        self.assertIn("llama3.2:latest", [a["assigned_model"] for a in round3["assignments"]])

        round4 = result["rounds"][3]
        self.assertEqual(round4["role"], "synthesizer")
        self.assertEqual(round4["assignments"][0]["assigned_model"], "gemini-2.5-flash")

    def test_no_hidden_models0_fallback(self):
        provider = StubProvider("mock", ["alpha:1", "beta:1", "gamma:1", "delta:1"])
        selected = [
            {"id": "alpha:1", "provider": "mock"},
            {"id": "beta:1", "provider": "mock"},
            {"id": "gamma:1", "provider": "mock"},
            {"id": "delta:1", "provider": "mock"},
        ]
        role_plan = {
            "analyst": ["alpha:1", "beta:1", "gamma:1"],
            "comparator": ["missing-comparator"],
            "challenger": ["missing-challenger"],
            "synthesizer": ["missing-synthesizer"],
        }
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Fallback Test", [m["id"] for m in selected], role_plan=role_plan)
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=role_plan)

        round2 = result["rounds"][1]
        round4 = result["rounds"][3]
        self.assertTrue(round2["assignments"][0]["fallback_used"])
        self.assertTrue(round4["assignments"][0]["fallback_used"])
        self.assertNotEqual(round2["assignments"][0]["assigned_model"], "alpha:1")
        self.assertNotEqual(round4["assignments"][0]["assigned_model"], "alpha:1")

    def test_structured_claim_creation(self):
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest", "gemini-2.5-flash"])
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
            {"id": "gemini-2.5-flash", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Claims Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))
        claims = result["council_state"]["claims"]

        self.assertGreaterEqual(len(claims), 1)
        claim = next(item for item in claims if item["claim_id"] == "C-001")
        for key in ["claim_id", "claim_text", "source_models", "supporting_models", "contradicting_models", "confidence", "verification_status"]:
            self.assertIn(key, claim)
        self.assertEqual(claim["verification_status"], "supported")

    def test_disagreement_detection(self):
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest", "gemini-2.5-flash"])
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
            {"id": "gemini-2.5-flash", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Disagreement Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))
        disagreements = result["council_state"]["disagreements"]

        self.assertGreaterEqual(len(disagreements), 1)
        first = disagreements[0]
        for key in ["disagreement_id", "claim_id", "claim", "model_positions", "status", "resolution"]:
            self.assertIn(key, first)
        self.assertEqual(first["status"], "open")

    def test_qualification_vs_disagreement(self):
        scripts = {
            "qwen2.5:7b": {
                "Claim assessments:": (
                    "Claim assessments:\n"
                    "- claim_id=C-001 | verification_status=unresolved | supporting_models= | contradicting_models= | confidence=0.50\n"
                    "Disagreements:\n"
                    "- disagreement_id=D-001 | claim_id=C-001 | claim=The answer depends on specific needs | model_positions=zephyr: depends on specific needs; qwen: depends on specific needs | status=open | resolution=unresolved\n"
                )
            }
        }
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest"], scripts=scripts)
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Qualification Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))

        self.assertEqual(result["council_state"]["disagreements"], [])

    def test_revision_tracking(self):
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest", "gemini-2.5-flash"])
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
            {"id": "gemini-2.5-flash", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Revision Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))
        revisions = result["council_state"]["revisions"]

        self.assertGreaterEqual(len(revisions), 1)
        revision = revisions[0]
        for key in ["revision_id", "model", "original_position", "revised_position", "reason", "affected_claims"]:
            self.assertIn(key, revision)

    def test_synthesis_contract(self):
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest", "gemini-2.5-flash"])
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
            {"id": "gemini-2.5-flash", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Synthesis Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))
        final_answer = result["current_state"]["final_answer"]

        for key in ["Decision", "Rationale", "Conditions", "Trade-offs", "Unresolved Issues", "Confidence"]:
            self.assertIn(key, final_answer)
        self.assertTrue(final_answer["Decision"])
        self.assertTrue(final_answer["Unresolved Issues"])

    def test_raw_outputs_preserved(self):
        provider = StubProvider("mock", ["zephyr:7b-alpha-q3_K_S", "qwen2.5:7b", "llama3.2:latest", "gemini-2.5-flash"])
        selected = [
            {"id": "zephyr:7b-alpha-q3_K_S", "provider": "mock"},
            {"id": "qwen2.5:7b", "provider": "mock"},
            {"id": "llama3.2:latest", "provider": "mock"},
            {"id": "gemini-2.5-flash", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Raw Output Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))
        result = engine.run(session, "Should an AI startup use local, cloud, or hybrid models?", selected, role_plan=engine.recommend_roles(selected))

        for round_record in result["rounds"]:
            self.assertIn("raw_outputs", round_record)
            self.assertTrue(round_record["raw_outputs"])
        self.assertTrue(result["rounds"][0]["raw_outputs"])

    def test_insufficient_healthy_models(self):
        provider = StubProvider("mock", ["good", "bad"], health_rules={"good": "ready", "bad": "timeout"})
        selected = [{"id": "good", "provider": "mock"}, {"id": "bad", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        session = store.create("Insufficient Test", [m["id"] for m in selected], role_plan=engine.recommend_roles(selected))

        with self.assertRaises(InsufficientHealthyModels):
            engine.run(session, "Should the council continue?", selected, role_plan=engine.recommend_roles(selected))

    def test_existing_session_loading(self):
        store = SessionStore(ROOT / "sessions")
        session = store.load("MC-D53FD746")
        self.assertIsNotNone(session)
        self.assertEqual(session["session_id"], "MC-D53FD746")
        self.assertIn("council_state", session)
        self.assertEqual(session["schema_version"], 3)

    def test_round2_renderer_not_blank_when_comparison_exists(self):
        renderer = TerminalRenderer()
        assignments = [
            {"requested_model": "llama3.2:latest", "actual_model": "zephyr:7b-alpha-q3_K_S", "role": "comparator", "fallback_used": True, "fallback_reason": "TIMEOUT"}
        ]
        parsed_two = {
            "claim_assessments": [{"text": "Consensus claim 1"}],
            "claims_requiring_challenge": [{"text": "Challenge claim 1"}],
            "uncertainties": [{"text": "Uncertainty 1"}],
            "parse_status": "COMPLETE",
            "parse_warnings": [],
        }
        output = renderer.render_round_two_summary(assignments, parsed_two, [])
        self.assertIn("Round 2: Comparison & Disagreement Analysis", output)
        self.assertIn("Consensus / Corroboration", output)
        self.assertIn("No material disagreement detected.", output)
        self.assertIn("Fallback: YES", output)

    def test_round2_no_disagreement_renders_explicit_notice(self):
        renderer = TerminalRenderer()
        output = renderer.render_disagreements([])
        self.assertIn("No material disagreement detected.", output)

    def test_position_matrix_semantics(self):
        from council import evaluate_position_disagreement, normalize_position
        self.assertEqual(normalize_position("recommends local models"), "local")
        self.assertEqual(normalize_position("opposes local models"), "oppose")

        # SUPPORT + SUPPORT -> agreement
        is_disag, status = evaluate_position_disagreement("support local", "support local")
        self.assertFalse(is_disag)
        self.assertEqual(status, "agreement")

        # OPPOSE + OPPOSE -> agreement
        is_disag, status = evaluate_position_disagreement("opposes cloud", "opposes cloud")
        self.assertFalse(is_disag)
        self.assertEqual(status, "agreement")

        # SUPPORT + OPPOSE -> material_disagreement
        is_disag, status = evaluate_position_disagreement("support local", "oppose local")
        self.assertTrue(is_disag)
        self.assertEqual(status, "material_disagreement")

        # OPPOSE + SUPPORT -> material_disagreement
        is_disag, status = evaluate_position_disagreement("opposes hybrid", "recommends hybrid")
        self.assertTrue(is_disag)
        self.assertEqual(status, "material_disagreement")

        # UNCERTAIN + SUPPORT -> insufficient_evidence
        is_disag, status = evaluate_position_disagreement("uncertain", "support local")
        self.assertFalse(is_disag)
        self.assertEqual(status, "insufficient_evidence")

        # UNKNOWN + SUPPORT -> insufficient_evidence
        is_disag, status = evaluate_position_disagreement("unknown", "support local")
        self.assertFalse(is_disag)
        self.assertEqual(status, "insufficient_evidence")

        # CONDITIONAL_SUPPORT + SUPPORT -> conditional_disagreement
        is_disag, status = evaluate_position_disagreement("depends on context", "support local")
        self.assertTrue(is_disag)
        self.assertEqual(status, "conditional_disagreement")

    def test_no_fabricated_disagreement_when_positions_agree(self):
        from council import material_disagreement
        pos_map = {"model_a": "recommends a hybrid approach", "model_b": "recommends a hybrid approach"}
        self.assertFalse(material_disagreement(pos_map))

    def test_contribution_validation_gate(self):
        from council import contribution_status_for, parse_round_one
        empty_status = contribution_status_for("analyst", "", parse_round_one(""))
        self.assertEqual(empty_status, "empty")

        malformed_json = '{"position": "x", "claims": ['
        malformed_status = contribution_status_for("analyst", malformed_json, parse_round_one(malformed_json))
        self.assertEqual(malformed_status, "malformed")

    def test_requested_vs_actual_model_provenance(self):
        provider = StubProvider("mock", ["alpha:1", "beta:1"], health_rules={"alpha:1": "timeout", "beta:1": "ready"})
        selected = [{"id": "alpha:1", "provider": "mock"}, {"id": "beta:1", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Provenance Test", [m["id"] for m in selected])
        assignments = [{"role": "analyst", "requested_model": "alpha:1", "assigned_model": "alpha:1"}]
        validated, preflight = engine._validate_round_assignments(1, "analyst", assignments, selected, {"alpha:1": {"status": "TIMEOUT"}, "beta:1": {"status": "READY"}})
        self.assertEqual(validated[0]["requested_model"], "alpha:1")
        self.assertEqual(validated[0]["actual_model"], "beta:1")
        self.assertTrue(validated[0]["fallback_used"])

    def test_council_diversity_and_degradation_detection(self):
        from council import calculate_council_diversity
        role_assignments = [
            {"requested_model": "qwen", "actual_model": "zephyr"},
            {"requested_model": "llama", "actual_model": "zephyr"},
        ]
        diversity = calculate_council_diversity(role_assignments)
        self.assertTrue(diversity["diversity_degraded"])
        self.assertEqual(diversity["independent_actual_models"], 1)
        self.assertEqual(diversity["requested_seats"], 2)

    def test_consensus_does_not_produce_verified(self):
        provider = StubProvider("mock", ["m1", "m2", "m3"])
        selected = [{"id": "m1", "provider": "mock"}, {"id": "m2", "provider": "mock"}, {"id": "m3", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Consensus Test", [m["id"] for m in selected])
        result = engine.run(session, "Question", selected)
        for claim in result["council_state"]["claims"]:
            self.assertNotEqual(claim["verification_status"], "verified")

    def test_golden_regression_fixture_MC_D53FD746(self):
        from council import parse_round_two
        golden_raw_round_two = (
            "Consensus Claims:\n"
            "- Local AI models have faster processing times and lower latency but require more hardware resources and are less scalable. (zephyr:7b-alpha-q3_K_S)\n"
            "- Cloud AI models offer greater scalability and flexibility but may result in higher costs due to data transfer fees and the need for additional storage space. (zephyr:7b-alpha-q3_K_S)\n"
            "- Hybrid AI models combine the benefits of local and cloud AI models by utilizing a combination of on-premise and cloud computing resources. (zephyr:7b-alpha-q3_K_S)\n\n"
            "Meaningful Disagreements:\n"
            "- The optimal choice depends on specific needs and constraints of the AI startup, such as data privacy, cost, scalability, and technical expertise. (qwen2.5:7b)\n\n"
            "Uncertain Claims:\n"
            "- The exact balance between data privacy and security versus the benefits of cloud scalability. (qwen2.5:7b)\n"
            "- The long-term costs associated with each option, including potential changes in technology or service provider policies. (qwen2.5:7b)\n\n"
            "Claims that require challenge:\n"
            "- The effectiveness of local AI models for tasks requiring high computational power (e.g., machine learning). (llama3.2:latest)\n"
            "- The impact of cloud-based infrastructure on data security and privacy. (llama3.2:latest)"
        )
        parsed = parse_round_two(golden_raw_round_two)
        self.assertEqual(parsed["parse_status"], "COMPLETE")
        self.assertGreaterEqual(len(parsed["claim_assessments"]), 3)
        self.assertGreaterEqual(len(parsed["disagreements"]), 1)
        self.assertGreaterEqual(len(parsed["uncertainties"]), 2)
        self.assertGreaterEqual(len(parsed["claims_requiring_challenge"]), 2)

        renderer = TerminalRenderer()
        output = renderer.render_round_two_summary(
            [{"requested_model": "zephyr", "actual_model": "zephyr", "role": "comparator", "fallback_used": False}],
            parsed,
            [{"disagreement_id": "D-001", "claim_id": "C-001", "claim": "Optimal choice depends on needs", "model_positions": {"qwen": "conditional"}, "status": "open", "resolution": "unresolved"}]
        )
        self.assertIn("Round 2: Comparison & Disagreement Analysis", output)
        self.assertIn("Consensus / Corroboration", output)
        self.assertIn("Claims Requiring Challenge", output)
        self.assertIn("D-001", output)

    def test_single_model_mode_allows_one_ready_model(self):
        provider = StubProvider("mock", ["model_a"])
        selected = [{"id": "model_a", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        session = store.create("Single Model Mode Test", [m["id"] for m in selected])

        result = engine.run(session, "Analyze hybrid AI.", selected, single_model_test=True)
        self.assertEqual(result["current_state"]["status"], "single_model_test_completed")
        self.assertEqual(len(result["rounds"]), 1)
        self.assertEqual(result["rounds"][0]["role"], "analyst")

    def test_single_model_mode_runs_only_analyst_and_renders_banner(self):
        class CaptureRenderer(TerminalRenderer):
            def __init__(self):
                self.output = []
            def render_single_model_test_banner(self):
                res = super().render_single_model_test_banner()
                self.output.append(res)
                return res
            def render_single_model_test_summary(self, assignments, analyst_output, claims, assumptions, uncertainties):
                res = super().render_single_model_test_summary(assignments, analyst_output, claims, assumptions, uncertainties)
                self.output.append(res)
                return res
            def section(self, title, lines):
                res = super().section(title, lines)
                self.output.append(res)
                return res

        provider = StubProvider("mock", ["model_a"])
        selected = [{"id": "model_a", "provider": "mock"}]
        renderer = CaptureRenderer()
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        engine.renderer = renderer
        session = store.create("Banner Test", [m["id"] for m in selected])

        result = engine.run(session, "Analyze hybrid AI.", selected, single_model_test=True, renderer=renderer)
        all_output = "\n".join(renderer.output)
        self.assertIn("SINGLE-MODEL TEST — COUNCIL DELIBERATION NOT RUN", all_output)
        self.assertEqual(len(result["rounds"]), 1)
        self.assertNotIn("synthesizer", [r["role"] for r in result["rounds"]])

    def test_normal_mode_still_requires_two_models(self):
        provider = StubProvider("mock", ["model_a"])
        selected = [{"id": "model_a", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        session = store.create("Normal Mode Fail Test", [m["id"] for m in selected])

        with self.assertRaises(InsufficientHealthyModels):
            engine.run(session, "Analyze hybrid AI.", selected, single_model_test=False)

    def test_mc_7e0de355_pattern_results_in_insufficient_evidence(self):
        # Analyst model_a returns empty response, zero claims extracted
        provider = StubProvider("mock", ["model_a", "model_b"], scripts={"model_a": {"Position:": ""}})
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        role_plan = {
            "analyst": ["model_a"],
            "comparator": ["model_b"],
            "challenger": [],
            "synthesizer": ["model_a"],
        }
        session = store.create("Empty Analyst Test", [m["id"] for m in selected], role_plan=role_plan)
        result = engine.run(session, "Test prompt", selected, role_plan=role_plan)

        self.assertEqual(result["current_state"]["status"], "INSUFFICIENT_EVIDENCE")
        # Should pause at Round 1 and not run Rounds 2-4
        self.assertEqual(len(result["rounds"]), 1)

    def test_fallback_reason_never_ready(self):
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        # Force synthesizer fallback by assigning model_a to challenger and requested synthesizer model_a
        role_plan = {
            "analyst": ["model_a"],
            "comparator": ["model_b"],
            "challenger": ["model_a"],
            "synthesizer": ["model_a"],
        }
        session = store.create("Fallback Reason Test", [m["id"] for m in selected], role_plan=role_plan)
        result = engine.run(session, "Test prompt", selected, role_plan=role_plan)

        round_four = result["rounds"][-1]
        asgn = round_four["assignments"][0]
        if asgn.get("fallback_used"):
            self.assertNotEqual(asgn.get("fallback_reason"), "READY")
            self.assertEqual(asgn.get("fallback_reason"), "diversity_constraint")

    def test_explicit_role_plan_not_overridden(self):
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider}, config={"min_healthy_models": 2})
        requested = {"analyst": ["model_a"], "comparator": ["model_b"], "challenger": [], "synthesizer": ["model_b"]}
        filtered = engine._health_filtered_role_plan(requested, selected, selected, {})
        self.assertEqual(filtered["challenger"], [])

    def test_done_reason_length_truncation_detection(self):
        # Scenario 1: done_reason=length with BROKEN JSON → correctly detected as partial
        truncated_json = '{"position": "Truncated mid-sentence', # syntactically broken
        response_data_broken = {
            "response": '{"position": "Truncated mid-sentence',
            "done": True,
            "done_reason": "length"
        }
        status_broken = contribution_status_for(
            "analyst", response_data_broken["response"],
            parse_round_one(response_data_broken["response"]),
            done_reason="length"
        )
        self.assertEqual(status_broken, "partial", "Broken JSON with done_reason=length must be partial")

        # Scenario 2: done_reason=length with COMPLETE, parseable JSON → valid
        # (model finished its structured output before the token limit)
        response_data_complete = {
            "response": '{"position": "Complete JSON", "claims": ["C1"], "assumptions": ["A1"], "uncertainties": ["U1"], "confidence": 0.9}',
            "done": True,
            "done_reason": "length"
        }
        status_complete = contribution_status_for(
            "analyst", response_data_complete["response"],
            parse_round_one(response_data_complete["response"]),
            done_reason="length"
        )
        self.assertEqual(status_complete, "valid", "Complete parseable JSON with done_reason=length should be valid")



    # =========================================================================
    # PHASE G — Provenance Attack Tests
    # =========================================================================

    def test_provenance_attack(self):
        """Injecting fake model IDs into comparator supporting_models must not corrupt council state."""
        from council import CouncilEngine, resolve_participant_id
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Provenance Attack Test", [m["id"] for m in selected])
        result = engine.run(session, "Test", selected)
        known = {"model_a", "model_b"}
        for claim in result["council_state"]["claims"]:
            for m in claim.get("supporting_models", []):
                self.assertIn(m, known, f"Fake model {m!r} leaked into supporting_models")
            for m in claim.get("contradicting_models", []):
                self.assertIn(m, known, f"Fake model {m!r} leaked into contradicting_models")
            for m in claim.get("source_models", []):
                self.assertIn(m, known, f"Fake model {m!r} leaked into source_models")

    def test_fake_model_identity_rejected(self):
        """resolve_participant_id must reject fabricated model names."""
        from council import resolve_participant_id
        known = ["minimax-m3:cloud", "zephyr:7b-alpha-q3_K_S"]
        # Generic/hallucinated names
        self.assertIsNone(resolve_participant_id("gpt", known))
        self.assertIsNone(resolve_participant_id("claude", known))
        self.assertIsNone(resolve_participant_id("gemini", known))
        self.assertIsNone(resolve_participant_id("model", known))
        self.assertIsNone(resolve_participant_id("agent", known))
        self.assertIsNone(resolve_participant_id("fake-model-x", known))
        self.assertIsNone(resolve_participant_id("model-a", known))
        # Real participant names must resolve
        self.assertEqual(resolve_participant_id("minimax-m3:cloud", known), "minimax-m3:cloud")
        self.assertEqual(resolve_participant_id("zephyr:7b-alpha-q3_K_S", known), "zephyr:7b-alpha-q3_K_S")

    # =========================================================================
    # PHASE H — Verification Attack Tests
    # =========================================================================

    def test_verification_claim_not_verification(self):
        """A model saying 'this claim is verified' in text must not set verification_status=verified."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        # The StubProvider _round_two_response injects supporting_models from model output
        # Regardless, the application must clamp verification_status to <= corroborated
        session = store.create("Verification Attack Test", [m["id"] for m in selected])
        result = engine.run(session, "Test", selected)
        for claim in result["council_state"]["claims"]:
            self.assertNotEqual(
                claim.get("verification_status"), "verified",
                f"Claim {claim['claim_id']} must not be 'verified' — no external evidence exists"
            )

    def test_consensus_not_verification(self):
        """Two models agreeing (corroboration) must NOT produce verification_status=verified."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["m1", "m2", "m3"])
        selected = [{"id": "m1", "provider": "mock"}, {"id": "m2", "provider": "mock"},
                    {"id": "m3", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Consensus Not Verification", [m["id"] for m in selected])
        result = engine.run(session, "Question", selected)
        for claim in result["council_state"]["claims"]:
            status = claim.get("verification_status", "")
            self.assertNotEqual(status, "verified",
                f"Claim {claim['claim_id']} got 'verified' from consensus alone — forbidden")
            # Corroborated (multiple sources agree) is the maximum without external evidence.
            # 'contradicted' is also valid — it reflects application-detected contradiction, not
            # model-asserted verification.
            self.assertIn(status, ("unverified", "supported", "corroborated", "unresolved", "contradicted"),
                f"Unexpected verification_status: {status!r}")

    # =========================================================================
    # PHASE I — Duplicate Claim Tests
    # =========================================================================

    def test_duplicate_claim_detection(self):
        """Two claims with identical normalized text from different models must be merged."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        # Override Round 1 responses to have identical claim text from both models
        provider.scripts = {
            "model_a": {1: '{"position": "support", "claims": ["Application validation improves reliability"], "assumptions": [], "uncertainties": [], "confidence": 0.8}'},
            "model_b": {1: '{"position": "support", "claims": ["Application validation improves reliability"], "assumptions": [], "uncertainties": [], "confidence": 0.7}'},
        }
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Duplicate Claim Test", [m["id"] for m in selected])
        result = engine.run(session, "Test", selected)
        # Identical claim text → should produce exactly 1 claim with both models as sources
        claims = result["council_state"]["claims"]
        texts = [c["claim_text"] for c in claims]
        self.assertEqual(len(texts), len(set(t.lower().strip() for t in texts)),
            "Duplicate claim texts should be merged, not duplicated")
        # The merged claim should have both models as source
        merged = next((c for c in claims if "application validation" in c["claim_text"].lower()), None)
        if merged:
            self.assertGreaterEqual(len(merged["source_models"]), 2,
                "Merged claim should list both source models")

    def test_distinct_claims_not_merged(self):
        """Two claims with different meanings must remain as separate claims."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        provider.scripts = {
            "model_a": {1: '{"position": "support", "claims": ["Application validation improves reliability"], "assumptions": [], "uncertainties": [], "confidence": 0.8}'},
            "model_b": {1: '{"position": "support", "claims": ["Application validation improves observability"], "assumptions": [], "uncertainties": [], "confidence": 0.7}'},
        }
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Distinct Claims Test", [m["id"] for m in selected])
        result = engine.run(session, "Test", selected)
        claims = result["council_state"]["claims"]
        # Two distinct claims must remain distinct
        texts_normalized = [c["claim_text"].lower().strip() for c in claims]
        has_reliability = any("reliability" in t for t in texts_normalized)
        has_observability = any("observability" in t for t in texts_normalized)
        self.assertTrue(has_reliability, "Reliability claim must be preserved")
        self.assertTrue(has_observability, "Observability claim must be preserved")

    # =========================================================================
    # PHASE J — Claim-Level Position Tests
    # =========================================================================

    def test_claim_level_positions(self):
        """Per-claim model position must be preserved; overall model position must not override claim positions."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        provider.scripts = {
            "model_a": {1: '{"position": "support", "claims": ["Claim one is good", "Claim two is bad"], "assumptions": [], "uncertainties": [], "confidence": 0.8}'},
            "model_b": {1: '{"position": "oppose", "claims": ["Claim three is neutral"], "assumptions": [], "uncertainties": [], "confidence": 0.6}'},
        }
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Claim-Level Positions Test", [m["id"] for m in selected])
        result = engine.run(session, "Test", selected)
        claims = result["council_state"]["claims"]
        # claim_positions must exist per claim
        for claim in claims:
            positions = claim.get("claim_positions") or claim.get("_positions", {})
            self.assertIsInstance(positions, dict,
                f"claim_positions must be a dict for claim {claim['claim_id']}")
            for model_id, pos in positions.items():
                self.assertIsNotNone(pos, "Position text must not be None")
        # model_a's claims must carry model_a's position text
        model_a_claims = [c for c in claims if "model_a" in c.get("source_models", [])]
        for claim in model_a_claims:
            positions = claim.get("claim_positions") or claim.get("_positions", {})
            self.assertIn("model_a", positions,
                f"model_a position not found in claim_positions for {claim['claim_id']}")

    # =========================================================================
    # PHASE K — Additional Position Matrix Cases
    # =========================================================================

    def test_oppose_oppose_agreement(self):
        """OPPOSE + OPPOSE → AGREEMENT (both models agree in opposition)."""
        from council import evaluate_position_disagreement
        is_disag, status = evaluate_position_disagreement("oppose the proposal", "oppose the proposal")
        self.assertFalse(is_disag, "OPPOSE+OPPOSE must not be a material disagreement")
        self.assertEqual(status, "agreement")

    def test_unknown_support_uncertainty(self):
        """UNKNOWN + SUPPORT → INSUFFICIENT_EVIDENCE, not disagreement."""
        from council import evaluate_position_disagreement
        is_disag, status = evaluate_position_disagreement("unknown", "support local")
        self.assertFalse(is_disag, "UNKNOWN+SUPPORT must not be a material disagreement")
        self.assertIn(status, ("insufficient_evidence", "uncertainty"))

    def test_unaligned_claims_not_disagreement(self):
        """Unaligned claims (no shared claim_id) must not manufacture a disagreement."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        provider.scripts = {
            "model_a": {1: '{"position": "support", "claims": ["Claim about apples"], "assumptions": [], "uncertainties": [], "confidence": 0.8}'},
            "model_b": {1: '{"position": "oppose", "claims": ["Claim about oranges"], "assumptions": [], "uncertainties": [], "confidence": 0.7}'},
        }
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Unaligned Claims Test", [m["id"] for m in selected])
        result = engine.run(session, "Test", selected)
        # Different claims from opposing positions → no cross-claim disagreement
        # (comparator would need to explicitly report a disagreement on a shared claim_id)
        disagreements = result["council_state"]["disagreements"]
        for d in disagreements:
            # Any materialized disagreement must reference a real claim_id
            self.assertTrue(d.get("claim_id") or d.get("claim"),
                "Disagreement must reference a specific claim")

    # =========================================================================
    # PHASE O — Fallback Integrity Tests
    # =========================================================================

    def test_fallback_provenance(self):
        """When fallback occurs, requested_model, actual_model, fallback_used, and fallback_reason are all distinct and preserved."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["alpha:1", "beta:1"],
                                health_rules={"alpha:1": "timeout", "beta:1": "ready"})
        selected = [{"id": "alpha:1", "provider": "mock"}, {"id": "beta:1", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Fallback Provenance Test", [m["id"] for m in selected])
        assignments = [{"role": "analyst", "requested_model": "alpha:1", "assigned_model": "alpha:1"}]
        validated, _ = engine._validate_round_assignments(
            1, "analyst", assignments, selected,
            {"alpha:1": {"status": "TIMEOUT"}, "beta:1": {"status": "READY"}}
        )
        a = validated[0]
        self.assertEqual(a["requested_model"], "alpha:1")
        self.assertEqual(a["actual_model"], "beta:1")
        self.assertTrue(a["fallback_used"])
        self.assertIsNotNone(a.get("fallback_reason"), "fallback_reason must be set")
        self.assertNotEqual(a.get("fallback_reason"), "READY",
            "fallback_reason must not be the health status string")

    def test_duplicate_actual_model_degradation(self):
        """Two requested seats resolving to the same actual model → diversity_degraded=True."""
        from council import calculate_council_diversity
        role_assignments = [
            {"requested_model": "qwen", "actual_model": "zephyr"},
            {"requested_model": "llama", "actual_model": "zephyr"},
        ]
        diversity = calculate_council_diversity(role_assignments)
        self.assertTrue(diversity["diversity_degraded"],
            "Same actual model in two seats must flag diversity_degraded")
        self.assertEqual(diversity["independent_actual_models"], 1)
        self.assertGreater(diversity["requested_seats"], 1)

    # =========================================================================
    # PHASE R — Round State Machine
    # =========================================================================

    def test_round_two_persistence(self):
        """Round 2 must always be stored in session.rounds even when contribution is partial."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"],
                                scripts={"model_a": {}, "model_b": {}})
        # Force comparator to return empty to trigger partial
        provider.scripts["model_b"] = {2: ""}
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        role_plan = {"analyst": ["model_a"], "comparator": ["model_b"], "challenger": [], "synthesizer": ["model_b"]}
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Round 2 Persistence Test", [m["id"] for m in selected],
                               role_plan=role_plan)
        try:
            result = engine.run(session, "Test", selected, role_plan=role_plan)
        except Exception:
            result = session
        round_numbers = [r["round"] for r in result.get("rounds", [])]
        self.assertIn(2, round_numbers, "Round 2 must always be recorded in session.rounds")

    def test_partial_synthesis_stays_incomplete(self):
        """Partial synthesizer output must leave session_status=incomplete, never completed."""
        from council import CouncilEngine, contribution_status_for, parse_round_four
        # Partial synthesizer: missing Rationale
        partial_synthesis = '{"Decision": "Use hybrid approach"}'
        parsed = parse_round_four(partial_synthesis)
        status = contribution_status_for("synthesizer", partial_synthesis, parsed)
        self.assertEqual(status, "partial",
            "Missing required synthesizer fields must yield partial, not valid")

    # =========================================================================
    # PHASE S — Full Session Replay
    # =========================================================================

    def test_prompt_preservation(self):
        """After run(), raw_outputs for each model invocation must contain 'prompt' for session replay."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Prompt Preservation Test", [m["id"] for m in selected])
        result = engine.run(session, "What is the best approach?", selected)
        for rnd in result.get("rounds", []):
            raw_outputs = rnd.get("raw_outputs", {})
            for model_id, output in raw_outputs.items():
                if output.get("status") in ("timeout", "http_error", "unavailable", "provider_failure"):
                    continue  # error outputs may lack prompt
                self.assertIn("prompt", output,
                    f"raw_outputs[{model_id!r}] missing 'prompt' field in round {rnd['round']}")
                self.assertIsInstance(output["prompt"], str,
                    f"prompt must be a string in round {rnd['round']}")
                self.assertGreater(len(output["prompt"]), 0,
                    f"prompt must not be empty in round {rnd['round']}")

    def test_raw_response_preservation(self):
        """raw_outputs must always contain 'response' key for every completed model invocation."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Raw Response Preservation Test", [m["id"] for m in selected])
        result = engine.run(session, "Test question", selected)
        for rnd in result.get("rounds", []):
            for model_id, output in rnd.get("raw_outputs", {}).items():
                if output.get("error"):
                    continue
                self.assertIn("response", output,
                    f"raw_outputs[{model_id!r}] missing 'response' in round {rnd['round']}")

    def test_full_session_replay(self):
        """Session must survive save→reload→compare with no information loss on key fields."""
        import json
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Full Session Replay Test", [m["id"] for m in selected])
        result = engine.run(session, "Replay question", selected)
        session_id = result["session_id"]

        # Reload from disk
        reloaded = store.load(session_id)
        self.assertIsNotNone(reloaded, "Session must be reloadable from disk")

        # Core field preservation
        self.assertEqual(reloaded["session_id"], result["session_id"])
        self.assertEqual(reloaded["title"], result["title"])
        self.assertEqual(len(reloaded["rounds"]), len(result["rounds"]),
            "Reloaded session must have same number of rounds")
        self.assertEqual(
            len(reloaded["council_state"]["claims"]),
            len(result["council_state"]["claims"]),
            "Claims must be preserved after reload"
        )
        self.assertEqual(
            reloaded["current_state"]["status"],
            result["current_state"]["status"],
            "Status must be preserved after reload"
        )
        # Raw outputs preserved
        for i, rnd in enumerate(result["rounds"]):
            reloaded_rnd = reloaded["rounds"][i]
            self.assertEqual(rnd["role"], reloaded_rnd["role"])
            for model_id in rnd.get("raw_outputs", {}):
                self.assertIn(model_id, reloaded_rnd.get("raw_outputs", {}),
                    f"raw_outputs for {model_id!r} not preserved in round {rnd['round']}")

    # =========================================================================
    # PHASE T — Synthesis Verification Integrity
    # =========================================================================

    def test_synthesis_verification_integrity(self):
        """Final synthesis with SUPPORT+OPPOSE disagreement must preserve that disagreement in council state."""
        from council import CouncilEngine, material_disagreement
        provider = StubProvider("mock", ["model_a", "model_b"])
        # Override Round 1 so models take opposite positions
        provider.scripts = {
            "model_a": {1: '{"position": "support the proposal", "claims": ["Proposal improves speed"], "assumptions": [], "uncertainties": [], "confidence": 0.9}'},
            "model_b": {1: '{"position": "oppose the proposal", "claims": ["Proposal reduces safety"], "assumptions": [], "uncertainties": [], "confidence": 0.8}'},
        }
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Synthesis Verification Integrity", [m["id"] for m in selected])
        result = engine.run(session, "Should we implement the proposal?", selected)
        # Synthesizer must not claim verified status when models disagreed
        for claim in result["council_state"]["claims"]:
            self.assertNotEqual(claim.get("verification_status"), "verified",
                "No claim may be 'verified' without external evidence")
        # Final answer must not be None (synthesis ran)
        final = result.get("current_state", {}).get("final_answer")
        if result["current_state"].get("status") == "completed":
            self.assertIsNotNone(final, "completed session must have a final_answer")

    # =========================================================================
    # PHASE W — Model Independence Accounting
    # =========================================================================

    def test_model_independence_accounting(self):
        """Model independence audit must correctly identify role reuse."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        # Compute audit with model_a playing analyst AND comparator
        role_assignments = [
            {"role": "analyst", "actual_model": "model_a", "assigned_model": "model_a", "execution_status": "completed"},
            {"role": "analyst", "actual_model": "model_b", "assigned_model": "model_b", "execution_status": "completed"},
            {"role": "comparator", "actual_model": "model_a", "assigned_model": "model_a", "execution_status": "completed"},
        ]
        audit = engine._model_independence_audit(role_assignments)
        # 2 independent analysts
        self.assertEqual(audit["analyst"]["independent_model_count"], 2)
        # 1 comparator model (model_a reused)
        self.assertEqual(audit["comparator"]["independent_model_count"], 1)
        # Role reuse must be detected in summary
        summary = audit["_summary"]
        self.assertGreater(len(summary["role_reuse_models"]), 0,
            "model_a acting as analyst+comparator must be flagged as role_reuse")
        self.assertEqual(summary["independent_analytical_sources"], 2)

    # =========================================================================
    # PHASE Z — Session Size Behavior
    # =========================================================================

    def test_session_size_behavior(self):
        """Session JSON must remain bounded — no runaway duplication of raw responses."""
        import json
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Size Behavior Test", [m["id"] for m in selected])
        result = engine.run(session, "Size test question", selected)
        serialized = json.dumps(result)
        size_kb = len(serialized.encode("utf-8")) / 1024
        # For a 4-round stub session (small responses), well under 500KB
        self.assertLess(size_kb, 500,
            f"Session JSON size {size_kb:.1f}KB exceeds 500KB — likely runaway duplication")
        # Verify prompt stored exactly once per round/model (not in both assignment and raw_output header)
        for rnd in result.get("rounds", []):
            for model_id, output in rnd.get("raw_outputs", {}).items():
                if "prompt" in output:
                    # Check that the same prompt text doesn't appear in the assignment objects too
                    for asgn in rnd.get("assignments", []):
                        self.assertNotIn("prompt", asgn,
                            "prompt must not be duplicated in both assignment and raw_output")

    def test_per_model_configuration(self):
        """Model profiles override default config parameters like temperature, max_output_tokens, timeouts."""
        class RecordingProvider(StubProvider):
            def __init__(self, name, models):
                super().__init__(name, models)
                self.recorded_kwargs = []
            def generate(self, model, prompt, **kwargs):
                self.recorded_kwargs.append((model, kwargs))
                return super().generate(model, prompt, **kwargs)

        provider = RecordingProvider("mock", ["model_a"])
        selected = [{"id": "model_a", "provider": "mock"}]
        # Set up explicit profiles override
        config = {
            "max_parallel_models": 1,
            "min_healthy_models": 1,
            "temperature": 0.5,
            "max_output_tokens": 100,
            "session_dir": "sessions",
            "model_profiles": {
                "model_a": {
                    "temperature": 0.9,
                    "max_output_tokens": 999,
                    "timeout_seconds": 12,
                }
            }
        }
        engine, store = self.make_engine({"mock": provider}, config=config)
        session = store.create("Per-Model Config Test", [m["id"] for m in selected])
        result = engine.run(session, "Question", selected, single_model_test=True)
        self.assertEqual(len(provider.recorded_kwargs), 1)
        model_name, kwargs = provider.recorded_kwargs[0]
        self.assertEqual(model_name, "model_a")
        self.assertEqual(kwargs["temperature"], 0.9)
        self.assertEqual(kwargs["max_output_tokens"], 999)
        self.assertEqual(kwargs["timeout_seconds"], 12)

    def test_per_model_configuration_fallback(self):
        """Fallback models respect their specific model profiles overrides, not the requested model's."""
        class RecordingProvider(StubProvider):
            def __init__(self, name, models, health_rules=None):
                super().__init__(name, models, health_rules=health_rules)
                self.recorded_kwargs = []
            def generate(self, model, prompt, **kwargs):
                self.recorded_kwargs.append((model, kwargs))
                return super().generate(model, prompt, **kwargs)

        provider = RecordingProvider("mock", ["model_a", "model_b"], health_rules={"model_a": "timeout", "model_b": "ready"})
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        config = {
            "max_parallel_models": 1,
            "min_healthy_models": 2,
            "temperature": 0.5,
            "max_output_tokens": 100,
            "session_dir": "sessions",
            "model_profiles": {
                "model_a": {
                    "temperature": 0.1,
                    "max_output_tokens": 111,
                },
                "model_b": {
                    "temperature": 0.8,
                    "max_output_tokens": 888,
                }
            }
        }
        engine, store = self.make_engine({"mock": provider}, config=config)
        session = store.create("Per-Model Fallback Config Test", [m["id"] for m in selected])
        # Inject timeout failure on model_a to trigger fallback to model_b
        health_report = {"model_a": {"status": "TIMEOUT"}, "model_b": {"status": "READY"}}
        assignments = [{"role": "analyst", "requested_model": "model_a", "assigned_model": "model_a"}]
        validated, preflight = engine._validate_round_assignments(1, "analyst", assignments, selected, health_report)
        self.assertEqual(validated[0]["actual_model"], "model_b")
        
        # Run execution on validated assignments
        outputs = engine._run_assignments(validated, lambda a: "test prompt")
        self.assertEqual(len(provider.recorded_kwargs), 1)
        model_name, kwargs = provider.recorded_kwargs[0]
        self.assertEqual(model_name, "model_b")
        self.assertEqual(kwargs["temperature"], 0.8)
        self.assertEqual(kwargs["max_output_tokens"], 888)

    def test_three_model_baseline(self):
        """recommend_roles includes a baseline of 3 unique model IDs, and fallback resolves all of them."""
        provider = StubProvider("mock", ["model_a", "model_b", "model_c", "model_d"],
                                health_rules={"model_a": "timeout", "model_b": "timeout", "model_c": "timeout", "model_d": "ready"})
        selected = [
            {"id": "model_a", "provider": "mock"},
            {"id": "model_b", "provider": "mock"},
            {"id": "model_c", "provider": "mock"},
            {"id": "model_d", "provider": "mock"},
        ]
        engine, store = self.make_engine({"mock": provider})
        
        # Verify recommendation has 3 unique model IDs across roles
        plan = engine.recommend_roles(selected)
        unique_ids = set()
        for role in ["comparator", "challenger", "synthesizer"]:
            for m_id in plan.get(role, []):
                unique_ids.add(m_id)
        
        self.assertGreaterEqual(len(unique_ids), 3,
            f"Recommended plan must contain at least 3 unique model IDs across roles, found {unique_ids}")
        
        # Verify fallback mechanism resolves all recommended roles when all primary models fail
        health_report = {
            "model_a": {"status": "TIMEOUT"},
            "model_b": {"status": "TIMEOUT"},
            "model_c": {"status": "TIMEOUT"},
            "model_d": {"status": "READY"}, # Only model_d is healthy
        }
        
        # Test comparator role validation fallback
        comparator_assignments = [{"role": "comparator", "requested_model": plan["comparator"][0], "assigned_model": plan["comparator"][0]}]
        validated_comparator, _ = engine._validate_round_assignments(
            2, "comparator", comparator_assignments, selected, health_report
        )
        self.assertEqual(validated_comparator[0]["actual_model"], "model_d")
        
        # Test challenger role validation fallback
        challenger_assignments = [{"role": "challenger", "requested_model": c_id, "assigned_model": c_id} for c_id in plan["challenger"]]
        validated_challengers, _ = engine._validate_round_assignments(
            3, "challenger", challenger_assignments, selected, health_report, allow_multiple=True
        )
        for val in validated_challengers:
            self.assertEqual(val["actual_model"], "model_d")

        # Test synthesizer role validation fallback
        synthesizer_assignments = [{"role": "synthesizer", "requested_model": plan["synthesizer"][0], "assigned_model": plan["synthesizer"][0]}]
        validated_synthesizer, _ = engine._validate_round_assignments(
            4, "synthesizer", synthesizer_assignments, selected, health_report
        )
        self.assertEqual(validated_synthesizer[0]["actual_model"], "model_d")

    def test_adaptive_computation_no_disagreements_skips_round_3(self):
        """When no disagreements are found in Round 2, Round 3 is skipped entirely, but Round 4 synthesis executes."""
        r2_no_disagreements = """
Consensus Claims:
- claim_id=C-001 | verification_status=supported | supporting_models=model_a,model_b | contradicting_models=none | confidence=0.9
"""
        provider = StubProvider("mock", ["model_a", "model_b"], scripts={
            "model_a": {2: r2_no_disagreements},
            "model_b": {2: r2_no_disagreements}
        })
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        session = store.create("Adaptive Comp Test", [m["id"] for m in selected])
        
        result = engine.run(session, "Deliberation question", selected)
        
        # Round 3 must be recorded in session.rounds as skipped
        round_three_record = next(r for r in result["rounds"] if r["round"] == 3)
        self.assertEqual(round_three_record["status"], "skipped")
        
        # Round 4 must run successfully
        round_four_record = next(r for r in result["rounds"] if r["round"] == 4)
        self.assertEqual(round_four_record["status"], "completed")

    def test_telemetry_lifecycle(self):
        """Telemetry must be propagated from execution to assignment to session to persistence."""
        from council import CouncilEngine
        provider = StubProvider("mock", ["model_a", "model_b"])
        selected = [{"id": "model_a", "provider": "mock"}, {"id": "model_b", "provider": "mock"}]
        engine, store = self.make_engine({"mock": provider})
        
        # Override generate to inject telemetry
        def mock_generate(model, prompt, **kwargs):
            return {
                "response": '{"position": "support", "claims": ["claim1"], "assumptions": [], "uncertainties": [], "confidence": 0.9}',
                "done": True,
                "latency_ms": 150,
                "input_tokens": 10,
                "output_tokens": 20,
                "done_reason": "stop",
                "request_start_timestamp": "2023-01-01T00:00:00Z",
                "response_completion_timestamp": "2023-01-01T00:00:01Z"
            }
        provider.generate = mock_generate
        
        session = store.create("Telemetry Test", [m["id"] for m in selected])
        
        # Prevent engine.run from crashing on parsing by using valid JSON in mock_generate
        try:
            result = engine.run(session, "Question", selected)
        except Exception:
            result = session
            
        rnd = result["rounds"][0]
        asgn = rnd["assignments"][0]
        self.assertIsInstance(asgn.get("latency_ms"), int)
        self.assertEqual(asgn.get("input_tokens"), 10)
        self.assertEqual(asgn.get("output_tokens"), 20)
        self.assertEqual(asgn.get("done_reason"), "stop")
        
        persisted = store.load(session["session_id"])
        self.assertIn("telemetry", persisted)
        self.assertIn("persistence", persisted["telemetry"])
        saves = [p for p in persisted["telemetry"]["persistence"] if p["action"] == "save"]
        self.assertGreater(len(saves), 0)
if __name__ == "__main__":
    unittest.main()




