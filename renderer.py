from __future__ import annotations


class TerminalRenderer:
    def section(self, title: str, lines: list[str]) -> str:
        body = "\n".join(lines) if lines else "(none)"
        return f"{title}\n{'-' * len(title)}\n{body}\n"

    def render_selected_models(self, selected: list[dict], role_plan: dict | None = None, health_report: dict | None = None) -> str:
        lines = []
        for model in selected:
            status = "UNKNOWN"
            reason = ""
            if health_report and model["id"] in health_report:
                status = health_report[model["id"]].get("status", status)
                reason = health_report[model["id"]].get("reason", "")
            suffix = f" - {reason}" if reason else ""
            lines.append(f"- {model['id']} [{model.get('provider', 'unknown')}] health={status}{suffix}")
        if role_plan:
            lines.append("")
            lines.append("Experimental role recommendation:")
            lines.append(f"- analyst: {', '.join(role_plan.get('analyst', [])) or '(none)'}")
            lines.append(f"- comparator: {', '.join(role_plan.get('comparator', [])) or '(none)'}")
            lines.append(f"- challenger: {', '.join(role_plan.get('challenger', [])) or '(none)'}")
            lines.append(f"- synthesizer: {', '.join(role_plan.get('synthesizer', [])) or '(none)'}")
        return self.section("Selected Models", lines)

    def render_health_report(self, health_report: dict[str, dict]) -> str:
        lines = []
        for model_id, info in health_report.items():
            reason = info.get("reason")
            lines.append(f"- {model_id}: {info.get('status', 'UNKNOWN')}{f' - {reason}' if reason else ''}")
        return self.section("Health Check", lines)

    def render_role_recommendation(self, recommendation: dict) -> str:
        lines = ["Experimental recommendations only. These are heuristic role assignments, not validated science."]
        for role in ("analyst", "comparator", "challenger", "synthesizer"):
            lines.append(f"- {role}: {', '.join(recommendation.get(role, [])) or '(none)'}")
        return self.section("Role Recommendation", lines)

    def render_round_header(self, round_number: int, title: str, role: str | None = None) -> str:
        suffix = f" [{role}]" if role else ""
        return f"\nRound {round_number}: {title}{suffix}\n{'=' * (9 + len(title) + len(suffix))}\n"

    def render_role_assignment_round(self, round_number: int, assignments: list[dict]) -> str:
        lines = []
        for assignment in assignments:
            line = f"- role={assignment.get('role')} requested_model={assignment.get('requested_model')} actual_model={assignment.get('actual_model', assignment.get('assigned_model'))} status={assignment.get('execution_status')}"
            if assignment.get("contribution_status"):
                line += f" contribution={assignment.get('contribution_status')}"
            if assignment.get("fallback_used"):
                line += f" fallback_from={assignment.get('fallback_from')} reason={assignment.get('fallback_reason')}"
            lines.append(line)
        return self.section(f"Round {round_number} Assignments", lines)

    def render_preflight(self, round_number: int, role: str, preflight: list[dict]) -> str:
        lines = []
        for item in preflight:
            lines.append(f"{role.title()}")
            lines.append(f"  requested -> {item.get('requested_model')}")
            lines.append(f"  status    -> {item.get('initial_status')}")
            for candidate in item.get("fallback_candidates", []):
                lines.append(
                    f"  candidate -> {candidate.get('model')} eligible={candidate.get('eligible')} score={candidate.get('score')} health={candidate.get('health')}"
                )
            if item.get("fallback_used"):
                lines.append(f"  selected  -> {item.get('selected_model')}")
            else:
                lines.append(f"  valid     -> {item.get('selected_model')}")
        return self.section(f"Round {round_number} Pre-flight", lines)

    def render_model_result(self, model_id: str, role: str, result: dict) -> str:
        lines = [f"Model: {model_id}", f"Role: {role}"]
        if result.get("status"):
            lines.append(f"Execution status: {result['status']}")
        if result.get("contribution_status"):
            lines.append(f"Contribution status: {result['contribution_status']}")
        if result.get("position"):
            lines.append(f"Position: {result['position']}")
        if result.get("claims"):
            lines.append("Claims:")
            for claim in result["claims"]:
                lines.append(f"  - {claim}")
        if result.get("assumptions"):
            lines.append("Assumptions:")
            for assumption in result["assumptions"]:
                lines.append(f"  - {assumption}")
        if result.get("uncertainties"):
            lines.append("Uncertainties:")
            for uncertainty in result["uncertainties"]:
                lines.append(f"  - {uncertainty}")
        if result.get("confidence") is not None:
            lines.append(f"Confidence: {result['confidence']}")
        if result.get("revisions"):
            lines.append("Revisions:")
            for revision in result["revisions"]:
                lines.append(f"  - {revision}")
        return self.section(f"{role.title()} Output", lines)

    def render_claims(self, claims: list[dict]) -> str:
        lines = []
        if not claims:
            lines.append("No claims.")
        for claim in claims:
            lines.append(f"- {claim['claim_id']}: {claim['claim_text']}")
            lines.append(f"  source_models: {', '.join(claim.get('source_models', [])) or '(none)'}")
            lines.append(f"  supporting_models: {', '.join(claim.get('supporting_models', [])) or '(none)'}")
            lines.append(f"  contradicting_models: {', '.join(claim.get('contradicting_models', [])) or '(none)'}")
            lines.append(f"  verification_status: {claim.get('verification_status', 'unverified')}")
            lines.append(f"  confidence: {claim.get('confidence', 0.0)}")
        return self.section("Claims", lines)

    def render_disagreements(self, disagreements: list[dict]) -> str:
        lines = []
        if not disagreements:
            lines.append("No material disagreement detected.")
        else:
            for item in disagreements:
                lines.append(f"- {item.get('disagreement_id', '(no id)')}: {item.get('claim', '')}")
                lines.append(f"  claim_id: {item.get('claim_id', '(none)')}")
                for model_id, position in (item.get("model_positions") or {}).items():
                    lines.append(f"  - {model_id}: {position}")
                lines.append(f"  status: {item.get('status', 'open')}")
                lines.append(f"  resolution: {item.get('resolution', 'unresolved')}")
        return self.section("Disagreements", lines)

    def render_round_two_summary(
        self,
        assignments: list[dict],
        parsed_two: dict,
        disagreements: list[dict],
        diversity_info: dict | None = None,
    ) -> str:
        lines = []
        if diversity_info and diversity_info.get("diversity_degraded"):
            lines.append("COUNCIL DEGRADED:")
            lines.append(
                f"{diversity_info.get('independent_actual_models', 0)} independent models available out of {diversity_info.get('requested_seats', 0)} requested seats."
            )
            lines.append("")

        lines.append("Participating Models & Roles:")
        for a in assignments:
            req = a.get("requested_model", "unknown")
            act = a.get("actual_model", a.get("assigned_model", "unknown"))
            role = a.get("role", "comparator")
            fb = "YES" if a.get("fallback_used") else "NO"
            reason = f" ({a.get('fallback_reason')})" if a.get("fallback_reason") else ""
            lines.append(f"  - Requested: {req} | Actual: {act} | Role: {role} | Fallback: {fb}{reason}")

        status = parsed_two.get("parse_status", "COMPLETE")
        lines.append(f"\nParse Status: {status}")
        for warn in parsed_two.get("parse_warnings", []):
            lines.append(f"  Warning: {warn}")

        claim_assessments = parsed_two.get("claim_assessments", [])
        if claim_assessments:
            lines.append("\nConsensus / Corroboration:")
            for ca in claim_assessments:
                text = ca.get("text") or ca.get("claim_text") or ca.get("claim_id") or str(ca)
                lines.append(f"  - {text}")

        challenge = parsed_two.get("claims_requiring_challenge", [])
        if challenge:
            lines.append("\nClaims Requiring Challenge:")
            for ch in challenge:
                text = ch.get("text") or ch.get("claim_text") or str(ch)
                lines.append(f"  - {text}")

        uncertainties = parsed_two.get("uncertainties", [])
        if uncertainties:
            lines.append("\nUncertainties:")
            for unc in uncertainties:
                text = unc.get("text") or unc.get("uncertainty") or str(unc)
                lines.append(f"  - {text}")

        lines.append("\nDisagreements:")
        if not disagreements:
            lines.append("  No material disagreement detected.")
        else:
            for item in disagreements:
                dis_id = item.get("disagreement_id", "(no id)")
                claim_txt = item.get("claim", "")
                lines.append(f"  - {dis_id}: {claim_txt}")
                lines.append(f"    claim_id: {item.get('claim_id', '(none)')}")
                for model_id, pos in (item.get("model_positions") or {}).items():
                    lines.append(f"    - {model_id}: {pos}")
                lines.append(f"    status: {item.get('status', 'open')}")
                lines.append(f"    resolution: {item.get('resolution', 'unresolved')}")

        return self.section("Round 2: Comparison & Disagreement Analysis", lines)

    def render_assumptions(self, assumptions: list[dict]) -> str:
        lines = []
        if not assumptions:
            lines.append("No assumptions.")
        for item in assumptions:
            lines.append(f"- {item.get('assumption_id', '(no id)')}: {item.get('assumption', '')}")
            lines.append(f"  source_models: {', '.join(item.get('source_models', [])) or '(none)'}")
            lines.append(f"  status: {item.get('status', 'unverified')}")
        return self.section("Assumptions", lines)

    def render_uncertainties(self, uncertainties: list[dict]) -> str:
        lines = []
        if not uncertainties:
            lines.append("No uncertainties.")
        for item in uncertainties:
            lines.append(f"- {item.get('uncertainty_id', '(no id)')}: {item.get('uncertainty', '')}")
            lines.append(f"  affected_claims: {', '.join(item.get('affected_claims', [])) or '(none)'}")
            lines.append(f"  source_models: {', '.join(item.get('source_models', [])) or '(none)'}")
            lines.append(f"  status: {item.get('status', 'open')}")
        return self.section("Uncertainties", lines)

    def render_revisions(self, revisions: list[dict]) -> str:
        lines = []
        if not revisions:
            lines.append("No revisions recorded.")
        for item in revisions:
            lines.append(f"- {item.get('revision_id', '(no id)')}: {item.get('model', '')}")
            lines.append(f"  original_position: {item.get('original_position', '')}")
            lines.append(f"  revised_position: {item.get('revised_position', '')}")
            lines.append(f"  reason: {item.get('reason', '')}")
            lines.append(f"  affected_claims: {', '.join(item.get('affected_claims', [])) or '(none)'}")
        return self.section("Revisions", lines)

    def render_final_decision(self, decision: dict) -> str:
        lines = []
        lines.append(f"Decision: {decision.get('Decision', '')}")
        lines.append(f"Rationale: {decision.get('Rationale', '')}")
        lines.append("Conditions:")
        for item in decision.get("Conditions", []):
            lines.append(f"  - {item}")
        lines.append("Trade-offs:")
        for item in decision.get("Trade-offs", []):
            lines.append(f"  - {item}")
        lines.append("Unresolved Issues:")
        for item in decision.get("Unresolved Issues", []):
            lines.append(f"  - {item}")
        lines.append(f"Confidence: {decision.get('Confidence', '')}")
        return self.section("Final Decision", lines)

    def render_status(self, session: dict | None, selected: list[dict], role_plan: dict | None = None) -> str:
        lines = []
        lines.append(f"Active session: {session.get('session_id') if session else 'none'}")
        if session:
            lines.append(f"Title: {session.get('title', 'Untitled')}")
            lines.append(f"Updated: {session.get('updated_at', 'n/a')}")
        lines.append(f"Selected models: {', '.join(model['id'] for model in selected) if selected else '(none)'}")
        lines.append(f"Role plan approved: {'yes' if role_plan else 'no'}")
        if session and session.get("current_state"):
            current = session["current_state"]
            lines.append(f"Last question: {current.get('last_question', '(none)')}")
            lines.append(f"Last round: {current.get('last_round', '(none)')}")
            if current.get("diversity_info", {}).get("diversity_degraded"):
                lines.append("Council Status: DEGRADED COUNCIL")
            if current.get("final_answer"):
                lines.append(f"Final decision: {current['final_answer'].get('Decision', '(none)')}")
            if current.get("council_state"):
                state = current["council_state"]
                lines.append(f"Claims: {len(state.get('claims', []))}")
                lines.append(f"Disagreements: {len(state.get('disagreements', []))}")
                lines.append(f"Revisions: {len(state.get('revisions', []))}")
        return self.section("Council Status", lines)

    def render_single_model_test_banner(self) -> str:
        return (
            "\nSINGLE-MODEL TEST — COUNCIL DELIBERATION NOT RUN\n"
            "=================================================\n"
        )

    def render_single_model_test_summary(
        self,
        assignments: list[dict],
        analyst_output: dict,
        claims: list[dict],
        assumptions: list[dict],
        uncertainties: list[dict],
    ) -> str:
        lines = [
            "SINGLE-MODEL TEST — COUNCIL DELIBERATION NOT RUN",
            "=================================================",
            "",
            "Tested Model Provenance:",
        ]
        for a in assignments:
            lines.append(
                f"  - Requested: {a.get('requested_model')} | Actual: {a.get('actual_model', a.get('assigned_model'))} | Locality: {a.get('execution_locality', 'local')}"
            )
            lines.append(
                f"  - Execution Status: {a.get('execution_status')} | Contribution Status: {a.get('contribution_status')}"
            )
            if a.get("latency_ms"):
                lines.append(f"  - Latency: {a.get('latency_ms')} ms")
        lines.append("")
        lines.append(f"Position: {analyst_output.get('position', '(none)')}")
        lines.append(f"Confidence: {analyst_output.get('confidence', '(none)')}")
        lines.append("")
        lines.append("Extracted Claims:")
        if not claims:
            lines.append("  (none)")
        for c in claims:
            lines.append(
                f"  - {c.get('claim_id', 'C-???')}: {c.get('claim_text', c.get('claim', ''))} [{c.get('verification_status', 'unverified')}]"
            )
        lines.append("")
        lines.append("Assumptions:")
        if not assumptions:
            lines.append("  (none)")
        for a in assumptions:
            lines.append(f"  - {a.get('assumption', '') if isinstance(a, dict) else a}")
        lines.append("")
        lines.append("Uncertainties:")
        if not uncertainties:
            lines.append("  (none)")
        for u in uncertainties:
            lines.append(f"  - {u.get('uncertainty', '') if isinstance(u, dict) else u}")
        return "\n".join(lines) + "\n"


