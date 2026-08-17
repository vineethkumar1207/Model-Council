from __future__ import annotations

import concurrent.futures
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


SYSTEM = """You are a participant in a controlled multi-model deliberation.
Stay strictly within the supplied task. Do not invent evidence. Distinguish facts,
inferences, assumptions, uncertainties, revisions, and recommendations.
Return concise, structured output.
"""

ROLE_ORDER = ("analyst", "comparator", "challenger", "synthesizer")
VERIFICATION_STATUSES = {"unverified", "supported", "model_supported", "corroborated", "verified", "contradicted", "unresolved"}
DISAGREEMENT_STATUSES = {"open", "resolved", "unresolved"}
ROUND_STATUSES = {"pending", "completed", "partial", "failed"}


class InsufficientHealthyModels(RuntimeError):
    pass


from parsers import (
    now,
    compact_json,
    normalize_text,
    split_bullets,
    normalize_structured_text,
    extract_section_text,
    split_key_values,
    split_map_items,
    parse_list_with_kv,
    parse_json_or_none,
    parse_json_strict,
    is_truncated_text,
    parse_round_one,
    parse_round_two,
    parse_round_three,
    parse_round_four,
    resolve_participant_id,
    _coerce_list,
    _coerce_string_list,
    _coerce_id_list,
    _to_float,
    profile_for_model,
    normalize_position,
    evaluate_position_disagreement,
    material_disagreement,
    classify_stance,
    calculate_council_diversity,
    contribution_status_for,
)
from executor import ModelExecutor
from policies import HealthPolicy, RoleAllocator, FallbackSelector


class CouncilEngine:
    def __init__(self, config, providers, store, renderer=None):
        self.config = config
        self.providers = providers
        self.store = store
        self.renderer = renderer
        self.executor = ModelExecutor(providers, config)
        self.health_policy = HealthPolicy(config)
        self.role_allocator = RoleAllocator(config, self.executor)
        self.fallback_selector = FallbackSelector(self.health_policy, self.role_allocator)
        self._model_registry = {}

    def discover_models(self):
        result = []
        for provider_name, provider in self.providers.items():
            try:
                result.extend(provider.list_models())
            except Exception as exc:
                print(f"[{provider_name}] discovery failed: {exc}")
        return result

    def provider_for_model(self, model_id):
        return self.executor.provider_for_model(model_id)

    def recommend_roles(self, models, health_report=None):
        return self.role_allocator.recommend_roles(models, health_report=health_report)

    def execution_locality_for_model(self, model):
        return self.health_policy.execution_locality_for_model(model)

    def classify_failure(self, exc):
        return self.health_policy.classify_failure(exc)

    def _health_filtered_role_plan(self, requested_plan, selected_models, healthy_models, health_report):
        recommended = self.recommend_roles(selected_models, health_report)
        if not requested_plan:
            return recommended

        filtered = {
            "experimental": True,
            "reason": "Requested role plan with health-aware pre-round validation. Unhealthy requested models remain provenance, not executable assignments.",
            "analyst": list(requested_plan.get("analyst", [])),
            "comparator": list(requested_plan.get("comparator", [])),
            "challenger": list(requested_plan.get("challenger", [])),
            "synthesizer": list(requested_plan.get("synthesizer", [])),
            "profiles": recommended.get("profiles", {}),
            "excluded_models": recommended.get("excluded_models", []),
            "requested_role_plan": requested_plan,
        }
        for role in ("analyst", "comparator", "challenger", "synthesizer"):
            if role not in requested_plan and not filtered[role]:
                filtered[role] = recommended.get(role, [])
        return filtered

    def _run_warmup_sequence(self, model, timeout_seconds, is_local):
        start = time.perf_counter()
        warmup_attempted = False
        warmup_duration = 0.0
        cold_start = False
        final_status = "UNKNOWN"
        reason = ""
        
        try:
            # Stage 1 — Fast health check
            self.executor.execute_health_check(model, timeout_seconds=timeout_seconds)
            initial_duration = time.perf_counter() - start
            final_status = "READY"
            reason = "Health check passed"
        except Exception as exc:
            initial_duration = time.perf_counter() - start
            status = self.classify_failure(exc)
            
            # Stage 2 — Cold-start warm-up (only for local models when timing out)
            if is_local and status == "TIMEOUT":
                warmup_attempted = True
                warmup_start = time.perf_counter()
                load_timeout = 45
                try:
                    self.executor.execute_health_check(model, timeout_seconds=load_timeout)
                    warmup_duration = time.perf_counter() - warmup_start
                    final_status = "READY"
                    cold_start = True
                    reason = "Health check passed after Stage 2 warm-up"
                except Exception as retry_exc:
                    warmup_duration = time.perf_counter() - warmup_start
                    final_status = self.classify_failure(retry_exc)
                    exc = retry_exc
            else:
                final_status = status

            if final_status != "READY":
                message = str(exc)
                lower = message.lower()
                if "out of memory" in lower or "cuda" in lower and "memory" in lower:
                    final_status = "FAILED"
                    reason = f"CUDA OOM detected: {message}"
                elif "http 500" in lower:
                    final_status = "FAILED"
                    reason = f"Provider HTTP 500: {message}"
                elif final_status == "TIMEOUT":
                    reason = f"Timeout: {message}"
                elif final_status == "UNAVAILABLE":
                    reason = f"Unavailable: {message}"
                else:
                    reason = f"Provider failure: {message}"

        total_latency = round((time.perf_counter() - start) * 1000)
        return final_status, reason, initial_duration, warmup_attempted, warmup_duration, cold_start, total_latency

    def health_check_model(self, model):
        provider_name = self.executor._provider_for_model_entry(model)
        if provider_name not in self.providers:
            return {
                "model_id": model["id"],
                "provider": provider_name,
                "status": "UNAVAILABLE",
                "reason": "No configured provider could load this model",
                "local_or_cloud": self.execution_locality_for_model(model),
                "cold_start": False,
                "warmup_attempted": False,
            }
        is_local = self.execution_locality_for_model(model) == "local"
        timeout_seconds = self.config.get("health_check_timeout_seconds", 20)
        
        final_status, reason, initial_duration, warmup_attempted, warmup_duration, cold_start, total_latency = self._run_warmup_sequence(
            model, timeout_seconds, is_local
        )
        
        result = {
            "model_id": model["id"],
            "provider": provider_name,
            "local_or_cloud": "local" if is_local else "cloud",
            "health_status": final_status,
            "status": final_status,  # Preserve backward compatibility
            "initial_check_duration": round(initial_duration, 3),
            "warmup_attempted": warmup_attempted,
            "warmup_duration": round(warmup_duration, 3) if warmup_attempted else None,
            "final_health_status": final_status,
            "cold_start": cold_start,
            "warmup_required": cold_start,
            "reason": reason,
            "latency_ms": total_latency,
        }
        if cold_start:
            result["health_warning"] = "cold_start"
            result["warning_reason"] = "cold_start"
        return result

    def health_check_selected_models(self, models):
        report = {}
        healthy = []
        for model in models:
            info = self.health_check_model(model)
            report[model["id"]] = info
            if info["status"] == "READY":
                healthy.append(model)
        return report, healthy

    def _call(self, model, prompt, timeout_seconds=None, role=None):
        return self.executor.execute(model, prompt, timeout_seconds=timeout_seconds, role=role)

    def _parallel(self, models, prompt_builder):
        outputs = {}
        if not models:
            return outputs
        max_workers = max(1, min(self.config.get("max_parallel_models", 1), len(models)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._call, model, prompt_builder(model)): model["id"] for model in models}
            for future in concurrent.futures.as_completed(futures):
                model_id = futures[future]
                try:
                    outputs[model_id] = future.result()
                except Exception as exc:
                    outputs[model_id] = {
                        "model": model_id,
                        "error": str(exc),
                        "status": self.classify_failure(exc),
                    }
        return outputs



    def _assignment_health_status(self, assignment, health_report):
        model_id = assignment.get("assigned_model")
        return health_report.get(model_id, {}).get("status", "UNKNOWN")

    def _assignment_is_valid(self, assignment, health_report, model_lookup, task_metadata=None):
        model_id = assignment.get("assigned_model")
        model = model_lookup.get(model_id)
        if not model:
            return False, "UNAVAILABLE"
        health = self._assignment_health_status(assignment, health_report)
        if health != "READY":
            return False, health
        if self.health_policy.violates_privacy(model, task_metadata):
            return False, "privacy constraint"
        if self.health_policy.violates_resources(model):
            return False, "resource constraint"
        return True, "READY"

    def _new_assignment(self, role, requested_model, actual_model, fallback_used=False, fallback_reason=None, candidates=None):
        selected_candidate = None
        for candidate in candidates or []:
            if candidate["model"] == actual_model["id"]:
                selected_candidate = candidate
                break
        return {
            "role": role,
            "requested_role": role,
            "requested_model": requested_model,
            "assigned_model": actual_model["id"],
            "actual_model": actual_model["id"],
            "execution_status": "ready",
            "contribution_status": "pending",
            "fallback_used": fallback_used,
            "fallback_from": requested_model if fallback_used else None,
            "fallback_reason": fallback_reason,
            "fallback_candidates": candidates or [],
            "fallback_selection_score": selected_candidate.get("score") if selected_candidate else None,
            "fallback_selection_method": "role_fitness",
            "execution_locality": self.health_policy.execution_locality_for_model(actual_model),
        }

    def _validate_round_assignments(
        self,
        round_number,
        role,
        assignments,
        models,
        health_report,
        exclude_ids=None,
        allow_multiple=False,
        task_metadata=None,
        renderer=None,
    ):
        model_lookup = self._model_lookup(models)
        exclude_ids = set(exclude_ids or [])
        validated = []
        preflight = []
        for assignment in assignments:
            requested_model = assignment.get("requested_model") or assignment.get("assigned_model")
            assigned = model_lookup.get(assignment.get("assigned_model"))
            if assigned:
                health_report[assigned["id"]] = self.health_check_model(assigned)
            valid, reason = self._assignment_is_valid(assignment, health_report, model_lookup, task_metadata)
            selected = assignment
            candidates = []
            if valid and assignment.get("assigned_model") not in exclude_ids:
                model = model_lookup[assignment["assigned_model"]]
                selected = self._new_assignment(role, requested_model, model)
            else:
                fallback_reason = reason
                if valid and assignment.get("assigned_model") in exclude_ids:
                    fallback_reason = "diversity_constraint"
                elif fallback_reason == "READY":
                    fallback_reason = "model_ineligible"
                candidates = self.fallback_selector.fallback_candidates(role, models, health_report, exclude_ids=exclude_ids, task_metadata=task_metadata)
                selected = None
                for candidate in candidates:
                    if not candidate["eligible"]:
                        continue
                    model = model_lookup[candidate["model"]]
                    recheck = self.health_check_model(model)
                    health_report[model["id"]] = recheck
                    if recheck.get("status") == "READY":
                        selected = self._new_assignment(
                            role,
                            requested_model,
                            model,
                            fallback_used=True,
                            fallback_reason=fallback_reason,
                            candidates=candidates,
                        )
                        break
                    candidate["eligible"] = False
                    candidate["health"] = recheck.get("status", "UNKNOWN")
                    candidate.setdefault("reasons", []).append("failed final revalidation")
            if selected:
                selected["round"] = round_number
                selected["model"] = model_lookup[selected["assigned_model"]]
                validated.append(selected)
                exclude_ids.add(selected["assigned_model"])
            preflight.append({
                "round": round_number,
                "role": role,
                "requested_model": requested_model,
                "initial_status": reason,
                "selected_model": selected.get("assigned_model") if selected else None,
                "fallback_used": bool(selected and selected.get("fallback_used")),
                "fallback_candidates": candidates,
            })
            if not allow_multiple:
                break
        if renderer and hasattr(renderer, "render_preflight"):
            self._render(renderer.render_preflight(round_number, role, preflight))
        return validated, preflight

    def _select_role_models(self, role, requested_ids, healthy_models, allow_multiple=False, exclude_ids=None):
        exclude_ids = set(exclude_ids or [])
        healthy_lookup = {model["id"]: model for model in healthy_models}
        assignments = []
        requested_ids = list(requested_ids or [])

        if allow_multiple:
            candidate_ids = requested_ids or [model["id"] for model in healthy_models]
            for requested_id in candidate_ids:
                model = healthy_lookup.get(requested_id)
                if model and model["id"] not in exclude_ids:
                    assignments.append({
                        "role": role,
                        "requested_model": requested_id,
                        "assigned_model": model["id"],
                        "execution_status": "ready",
                        "fallback_used": False,
                        "fallback_from": None,
                        "fallback_reason": None,
                    })
                    exclude_ids.add(model["id"])
                    continue
                assignments.append({
                    "role": role,
                    "requested_model": requested_id,
                    "assigned_model": requested_id,
                    "execution_status": "pending_validation",
                    "fallback_used": False,
                    "fallback_from": None,
                    "fallback_reason": None,
                })
            return assignments

        preferred = None
        for requested_id in requested_ids:
            model = healthy_lookup.get(requested_id)
            if model and model["id"] not in exclude_ids:
                preferred = model
                break
        if requested_ids and preferred is None:
            return [{
                "role": role,
                "requested_model": requested_ids[0],
                "assigned_model": requested_ids[0],
                "execution_status": "pending_validation",
                "fallback_used": False,
                "fallback_from": None,
                "fallback_reason": None,
            }]
        if preferred is None:
            preferred = self.fallback_selector.fallback_model(role, healthy_models, exclude_ids=exclude_ids)
        if preferred is None:
            return []
        fallback_used = requested_ids and preferred["id"] != requested_ids[0]
        assignments.append({
            "role": role,
            "requested_model": requested_ids[0] if requested_ids else None,
            "assigned_model": preferred["id"],
            "execution_status": "fallback_used" if fallback_used else "ready",
            "fallback_used": fallback_used,
            "fallback_from": requested_ids[0] if fallback_used and requested_ids else None,
            "fallback_reason": "Requested model unavailable or filtered out" if fallback_used else None,
        })
        return assignments

    def _model_lookup(self, models):
        return {model["id"]: model for model in models}

    def _assignment_display(self, assignment):
        return {
            "round": assignment.get("round"),
            "role": assignment.get("role"),
            "requested_role": assignment.get("requested_role", assignment.get("role")),
            "requested_model": assignment.get("requested_model"),
            "assigned_model": assignment.get("assigned_model"),
            "actual_model": assignment.get("actual_model", assignment.get("assigned_model")),
            "execution_status": assignment.get("execution_status"),
            "contribution_status": assignment.get("contribution_status", "pending"),
            "fallback_used": assignment.get("fallback_used", False),
            "fallback_from": assignment.get("fallback_from"),
            "fallback_reason": assignment.get("fallback_reason"),
            "fallback_candidates": assignment.get("fallback_candidates", []),
            "fallback_selection_score": assignment.get("fallback_selection_score"),
            "fallback_selection_method": assignment.get("fallback_selection_method"),
            "execution_locality": assignment.get("execution_locality"),
        }

    def _render(self, text):
        if self.renderer:
            try:
                print(text)
            except UnicodeEncodeError:
                import sys
                enc = sys.stdout.encoding or "ascii"
                try:
                    print(text.encode(enc, errors="replace").decode(enc))
                except Exception:
                    print(text.encode("ascii", errors="replace").decode("ascii"))

    def _checkpoint(self, session):
        self.store.save(session)

    def _ensure_state(self, session):
        session.setdefault("current_state", {})
        session.setdefault("council_state", {})
        session.setdefault("rounds", [])
        session.setdefault("messages", [])
        session.setdefault("health_report", {})
        session.setdefault("role_plan", {})
        session.setdefault("approved_role_plan", None)

    def _new_claim_id(self, index):
        return f"C-{index:03d}"

    def _new_id(self, prefix, index):
        return f"{prefix}-{index:03d}"

    def _add_or_merge_claim(self, claim_registry, claim_lookup, claim_text, model_id, position_text, confidence):
        key = normalize_text(claim_text)
        claim = claim_lookup.get(key)
        if claim is None:
            claim_id = self._new_claim_id(len(claim_registry) + 1)
            claim = {
                "claim_id": claim_id,
                "claim_text": claim_text.strip(),
                "source_models": [model_id],
                "supporting_models": [],
                "contradicting_models": [],
                "confidence": confidence if confidence is not None else 0.0,
                "verification_status": "unverified",
                # claim_positions: per-model position text (public field for session replay)
                "claim_positions": {model_id: position_text},
            }
            claim_lookup[key] = claim
            claim_registry.append(claim)
        else:
            if model_id not in claim["source_models"]:
                claim["source_models"].append(model_id)
            if confidence is not None:
                claim["confidence"] = max(claim.get("confidence", 0.0), confidence)
            claim["claim_positions"][model_id] = position_text
        return claim

    def _build_state_from_round_one(self, round_one_results):
        claims = []
        assumptions = []
        uncertainties = []
        claim_lookup = {}
        model_to_claim_ids = {}

        for model_id, result in round_one_results.items():
            parsed = result.get("parsed", {})
            position_text = parsed.get("position", "")
            claim_ids = []
            for claim_text in parsed.get("claims", []):
                claim = self._add_or_merge_claim(
                    claims,
                    claim_lookup,
                    claim_text,
                    model_id,
                    position_text,
                    parsed.get("confidence"),
                )
                claim_ids.append(claim["claim_id"])
            model_to_claim_ids[model_id] = claim_ids

        for model_id, result in round_one_results.items():
            parsed = result.get("parsed", {})
            source_claims = model_to_claim_ids.get(model_id, [])
            for assumption_text in parsed.get("assumptions", []):
                assumptions.append({
                    "assumption_id": self._new_id("A", len(assumptions) + 1),
                    "assumption": assumption_text,
                    "source_models": [model_id],
                    "status": "unverified",
                    "affected_claims": source_claims[:],
                })
            for uncertainty_text in parsed.get("uncertainties", []):
                uncertainties.append({
                    "uncertainty_id": self._new_id("U", len(uncertainties) + 1),
                    "uncertainty": uncertainty_text,
                    "affected_claims": source_claims[:],
                    "source_models": [model_id],
                    "status": "open",
                })

        return claims, assumptions, uncertainties, model_to_claim_ids

    def _apply_claim_assessments(self, claims, assessments, known_participant_ids=None):
        lookup = {claim["claim_id"]: claim for claim in claims}
        for assessment in assessments:
            claim_id = assessment.get("claim_id") or assessment.get("claimId")
            if not claim_id or claim_id not in lookup:
                continue
            claim = lookup[claim_id]
            status = str(assessment.get("verification_status", assessment.get("status", "unverified"))).strip().lower()
            if status not in VERIFICATION_STATUSES:
                status = "unverified"
            supporting = _coerce_id_list(assessment.get("supporting_models") or assessment.get("supporting"))
            contradicting = _coerce_id_list(assessment.get("contradicting_models") or assessment.get("contradicting"))
            if known_participant_ids:
                supporting = [resolve_participant_id(m, known_participant_ids) for m in supporting if resolve_participant_id(m, known_participant_ids)]
                contradicting = [resolve_participant_id(m, known_participant_ids) for m in contradicting if resolve_participant_id(m, known_participant_ids)]
            if supporting:
                claim["supporting_models"] = self._merge_unique(claim["supporting_models"], supporting)
            if contradicting:
                claim["contradicting_models"] = self._merge_unique(claim["contradicting_models"], contradicting)
            if "confidence" in assessment:
                claim["confidence"] = assessment["confidence"] if assessment["confidence"] is not None else claim.get("confidence", 0.0)

            distinct_supporters = set(claim.get("source_models", [])) | set(claim.get("supporting_models", []))
            distinct_contradictors = set(claim.get("contradicting_models", []))
            if status == "verified":
                if len(distinct_supporters) >= 2:
                    status = "corroborated"
                elif len(distinct_supporters) == 1:
                    status = "supported"
                else:
                    status = "unverified"
            elif status == "unverified" and len(distinct_supporters) >= 2:
                status = "corroborated"
            elif status == "unverified" and len(distinct_supporters) == 1:
                status = "supported"

            if len(distinct_contradictors) > 0:
                status = "unresolved"

            claim["verification_status"] = status
        return claims

    def _merge_unique(self, existing, new_items):
        merged = list(existing or [])
        for item in new_items:
            if item not in merged:
                merged.append(item)
        return merged

    def _materialize_disagreements(self, disagreements, claims, known_participant_ids=None):
        claim_lookup = {claim["claim_id"]: claim for claim in claims}
        records = []
        for item in disagreements:
            if not isinstance(item, dict):
                continue
            claim_id = item.get("claim_id") or item.get("claimId")
            claim_text = item.get("claim") or ""
            model_positions = item.get("model_positions") or item.get("positions") or {}
            if isinstance(model_positions, str):
                model_positions = split_map_items(model_positions)
            if known_participant_ids and isinstance(model_positions, dict):
                resolved_positions = {}
                for m, pos in model_positions.items():
                    resolved_id = resolve_participant_id(m, known_participant_ids)
                    if resolved_id:
                        resolved_positions[resolved_id] = pos
                model_positions = resolved_positions
            if claim_id and claim_id in claim_lookup:
                claim = claim_lookup[claim_id]
            else:
                claim = {
                    "claim_id": claim_id or "",
                    "claim_text": claim_text,
                }
            if not material_disagreement(model_positions):
                continue
            record = {
                "disagreement_id": item.get("disagreement_id") or item.get("id") or self._new_id("D", len(records) + 1),
                "claim_id": claim.get("claim_id", claim_id or ""),
                "claim": claim.get("claim_text", claim_text),
                "model_positions": model_positions,
                "status": str(item.get("status", "open")).strip().lower() if str(item.get("status", "open")).strip().lower() in DISAGREEMENT_STATUSES else "open",
                "resolution": item.get("resolution", "unresolved"),
            }
            records.append(record)
        return records

    def _materialize_revisions(self, revisions, known_participant_ids=None):
        records = []
        for item in revisions:
            if not isinstance(item, dict):
                continue
            model = item.get("model") or item.get("model_id") or item.get("modelId")
            resolved_id = resolve_participant_id(model, known_participant_ids) if known_participant_ids else model
            if not resolved_id:
                continue
            model = resolved_id
            original = item.get("original_position") or item.get("original") or ""
            revised = item.get("revised_position") or item.get("revision") or item.get("revised") or ""
            if not (model and revised):
                continue
            if original and normalize_text(original) == normalize_text(revised):
                continue
            records.append({
                "revision_id": item.get("revision_id") or self._new_id("R", len(records) + 1),
                "model": model,
                "original_position": original,
                "revised_position": revised,
                "reason": item.get("reason", ""),
                "affected_claims": _coerce_id_list(item.get("affected_claims") or item.get("claims")),
            })
        return records

    def _verification_summary(self, claims, disagreements):
        claim_map = {claim["claim_id"]: claim["verification_status"] for claim in claims}
        disagreement_map = {item["disagreement_id"]: item["status"] for item in disagreements}
        return {
            "claims": claim_map,
            "disagreements": disagreement_map,
            "counts": {
                "claims": len(claims),
                "disagreements": len(disagreements),
            },
        }

    def _model_independence_audit(self, role_assignments):
        """Compute per-role unique actual model counts for transparency reporting.
        A single model playing multiple roles does NOT count as independent evidence."""
        audit = {}
        for role in ROLE_ORDER:
            models_in_role = [
                a.get("actual_model") or a.get("assigned_model")
                for a in role_assignments
                if a.get("role") == role and a.get("execution_status") == "completed"
            ]
            unique = list(dict.fromkeys(m for m in models_in_role if m))
            audit[role] = {
                "requested_seats": sum(1 for a in role_assignments if a.get("role") == role),
                "actual_models": unique,
                "independent_model_count": len(unique),
            }
        # Cross-role: analyst models that also appear as comparator/synthesizer
        analyst_models = set(audit.get("analyst", {}).get("actual_models", []))
        comparator_models = set(audit.get("comparator", {}).get("actual_models", []))
        synthesizer_models = set(audit.get("synthesizer", {}).get("actual_models", []))
        role_reuse = list(analyst_models & (comparator_models | synthesizer_models))
        audit["_summary"] = {
            "independent_analytical_sources": len(analyst_models),
            "role_reuse_models": role_reuse,
            "note": (
                "Role reuse detected: same model contributes as analyst AND comparator/synthesizer. "
                "These are NOT independent evidence sources."
            ) if role_reuse else "No role reuse detected.",
        }
        return audit

    def _compute_information_gain(self, round_one_results, prior_claims):
        """EXPERIMENTAL: Estimate information added by each analyst contribution.
        Not scientifically validated. Never influences routing or decisions."""
        prior_texts = {normalize_text(c["claim_text"]) for c in prior_claims}
        per_model = {}
        all_new_texts = set()
        for model_id, result in round_one_results.items():
            parsed = result.get("parsed", {}) or {}
            claims = parsed.get("claims", []) or []
            new = [t for t in claims if normalize_text(t) not in prior_texts and normalize_text(t) not in all_new_texts]
            dup = [t for t in claims if normalize_text(t) in prior_texts or normalize_text(t) in all_new_texts]
            all_new_texts.update(normalize_text(t) for t in new)
            per_model[model_id] = {
                "new_claims": len(new),
                "duplicate_claims": len(dup),
                "total_claims": len(claims),
            }
        total_new = sum(v["new_claims"] for v in per_model.values())
        total_dup = sum(v["duplicate_claims"] for v in per_model.values())
        total = total_new + total_dup
        score = round(total_new / total, 3) if total > 0 else 0.0
        return {
            "_label": "EXPERIMENTAL INFORMATION-GAIN METRIC",
            "_warning": "Not scientifically validated. Never influences decisions.",
            "per_model": per_model,
            "total_new_claims": total_new,
            "total_duplicate_claims": total_dup,
            "information_gain_score": score,
        }

    def _store_council_state(self, session, claims, disagreements, assumptions, uncertainties, revisions, role_assignments, information_gain=None, model_independence=None):
        session["council_state"] = {
            "claims": claims,
            "disagreements": disagreements,
            "assumptions": assumptions,
            "uncertainties": uncertainties,
            "revisions": revisions,
            "role_assignments": role_assignments,
            "verification_summary": self._verification_summary(claims, disagreements),
            "model_independence": model_independence or {},
            "information_gain": information_gain or {},
        }

    def _compact_council_state(self, council_state):
        return {
            "claims": [
                {
                    "claim_id": claim["claim_id"],
                    "claim_text": claim["claim_text"],
                    "source_models": claim["source_models"],
                    "supporting_models": claim["supporting_models"],
                    "contradicting_models": claim["contradicting_models"],
                    "confidence": claim["confidence"],
                    "verification_status": claim["verification_status"],
                    # claim_positions: per-model position text preserved for session replay
                    "claim_positions": claim.get("claim_positions") or claim.get("_positions") or {},
                }
                for claim in council_state.get("claims", [])
            ],
            "disagreements": [
                {
                    "disagreement_id": item["disagreement_id"],
                    "claim_id": item["claim_id"],
                    "claim": item["claim"],
                    "model_positions": item["model_positions"],
                    "status": item["status"],
                    "resolution": item["resolution"],
                }
                for item in council_state.get("disagreements", [])
            ],
            "assumptions": [
                {
                    "assumption_id": item["assumption_id"],
                    "assumption": item["assumption"],
                    "source_models": item["source_models"],
                    "status": item["status"],
                }
                for item in council_state.get("assumptions", [])
            ],
            "uncertainties": [
                {
                    "uncertainty_id": item["uncertainty_id"],
                    "uncertainty": item["uncertainty"],
                    "affected_claims": item["affected_claims"],
                    "source_models": item["source_models"],
                    "status": item["status"],
                }
                for item in council_state.get("uncertainties", [])
            ],
            "revisions": [
                {
                    "revision_id": item["revision_id"],
                    "model": item["model"],
                    "original_position": item["original_position"],
                    "revised_position": item["revised_position"],
                    "reason": item["reason"],
                    "affected_claims": item["affected_claims"],
                }
                for item in council_state.get("revisions", [])
            ],
            "role_assignments": council_state.get("role_assignments", []),
            "verification_summary": council_state.get("verification_summary", {}),
            "model_independence": council_state.get("model_independence", {}),
            "information_gain": council_state.get("information_gain", {}),
        }

    def _render_round(self, round_number, role, assignments, round_status):
        if not self.renderer:
            return
        lines = [f"Round {round_number}", f"Role: {role}", f"Status: {round_status}"]
        for assignment in assignments:
            lines.append(
                f"- {assignment['assigned_model']} | status={assignment['execution_status']}"
                + (f" | fallback_from={assignment['fallback_from']}" if assignment.get("fallback_from") else "")
            )
        self._render(self.renderer.section(f"Round {round_number}", lines[1:]))

    def _record_round(self, session, round_number, role, assignments, raw_outputs, structured, round_status):
        round_record = {
            "round": round_number,
            "role": role,
            "assignments": [
                {
                    "round": round_number,
                    "role": assignment["role"],
                    "requested_role": assignment.get("requested_role", assignment["role"]),
                    "requested_model": assignment.get("requested_model"),
                    "assigned_model": assignment["assigned_model"],
                    "actual_model": assignment.get("actual_model", assignment["assigned_model"]),
                    "execution_status": assignment["execution_status"],
                    "contribution_status": assignment.get("contribution_status", "pending"),
                    "fallback_used": assignment.get("fallback_used", False),
                    "fallback_from": assignment.get("fallback_from"),
                    "fallback_reason": assignment.get("fallback_reason"),
                    "fallback_candidates": assignment.get("fallback_candidates", []),
                    "fallback_selection_score": assignment.get("fallback_selection_score"),
                    "fallback_selection_method": assignment.get("fallback_selection_method"),
                    "execution_locality": assignment.get("execution_locality"),
                    "latency_ms": assignment.get("latency_ms"),
                    "input_tokens": assignment.get("input_tokens"),
                    "output_tokens": assignment.get("output_tokens"),
                    "done_reason": assignment.get("done_reason"),
                    "latency_detail": assignment.get("latency_detail"),
                    "request_start_timestamp": assignment.get("request_start_timestamp"),
                    "response_completion_timestamp": assignment.get("response_completion_timestamp"),
                }
                for assignment in assignments
            ],
            "raw_outputs": raw_outputs,
            "structured": structured,
            "status": round_status,
            "created_at": now(),
        }
        session["rounds"].append(round_record)
        session["current_state"]["last_round"] = round_number
        session["current_state"]["round_status"] = round_status
        session["current_state"]["role"] = role
        self._checkpoint(session)
        return round_record

    def _round_result_status(self, assignments):
        statuses = {assignment["execution_status"] for assignment in assignments}
        if statuses == {"completed"}:
            return "completed"
        if "completed" in statuses:
            return "partial"
        if statuses == {"fallback_used"}:
            return "completed"
        return "failed"

    def _assignment_prompt_role(self, role, model_id):
        return f"You are model {model_id} acting as a {role}."

    def _run_assignments(self, assignments, prompt_builder):
        outputs = {}
        if not assignments:
            return outputs
        max_workers = max(1, min(self.config.get("max_parallel_models", 1), len(assignments)))
        # Capture prompts before submitting so they can be stored for session replay
        assignment_prompts = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for assignment in assignments:
                model = assignment["model"]
                prompt = prompt_builder(assignment)
                assignment_prompts[id(assignment)] = prompt
                futures[pool.submit(self._call, model, prompt, role=assignment.get("role"))] = assignment
            for future in concurrent.futures.as_completed(futures):
                assignment = futures[future]
                model_id = assignment["assigned_model"]
                prompt = assignment_prompts.get(id(assignment), "")
                try:
                    result = future.result()
                    assignment["execution_status"] = "completed"
                    assignment["latency_ms"] = result.get("latency_ms")
                    assignment["input_tokens"] = result.get("input_tokens")
                    assignment["output_tokens"] = result.get("output_tokens")
                    assignment["done_reason"] = result.get("done_reason")
                    assignment["latency_detail"] = result.get("latency_detail")
                    assignment["request_start_timestamp"] = result.get("request_start_timestamp")
                    assignment["response_completion_timestamp"] = result.get("response_completion_timestamp")
                    # Store prompt and timestamp for session replay (Phase S)
                    result["prompt"] = prompt
                    result["timestamp"] = now()
                    outputs[model_id] = result
                except Exception as exc:
                    assignment["execution_status"] = self.classify_failure(exc)
                    assignment["error"] = str(exc)
                    outputs[model_id] = {
                        "model": model_id,
                        "error": str(exc),
                        "status": assignment["execution_status"],
                        "prompt": prompt,
                        "timestamp": now(),
                    }
                    assignment["contribution_status"] = "malformed"
        return outputs

    def _run_phase_1(self, session, question, healthy_models, models, renderer, progress_hook, single_model_test, session_start_perf):
        import time
        if single_model_test:
            analyst_ids = [healthy_models[0]["id"]]
            analyst_assignments = self._select_role_models("analyst", analyst_ids, healthy_models, allow_multiple=False)
            analyst_assignments, analyst_preflight = self._validate_round_assignments(
                1,
                "analyst",
                analyst_assignments,
                models,
                session["health_report"],
                allow_multiple=False,
                renderer=renderer,
            )
            analyst_prompt = self._build_round_one_prompt(session, question, healthy_models)
            if renderer:
                self._render(renderer.render_round_header(1, "Single-Model Independent Analysis", "analyst"))
            round_one_outputs = self._run_assignments(analyst_assignments, lambda assignment: analyst_prompt)
            round_one_structured = {}
            for assignment in analyst_assignments:
                model_id = assignment["assigned_model"]
                raw_res = round_one_outputs.get(model_id, {})
                raw_text = raw_res.get("response", "")
                parsed = parse_round_one(raw_text)
                contrib_status = contribution_status_for("analyst", raw_text, parsed, done_reason=raw_res.get("done_reason"))
                assignment["contribution_status"] = contrib_status
                round_one_structured[model_id] = parsed
            round_status = self._round_result_status(analyst_assignments)
            self._record_round(session, 1, "analyst", analyst_assignments, round_one_outputs, round_one_structured, round_status)
            claims, assumptions, uncertainties, model_to_claim_ids = self._build_state_from_round_one(round_one_outputs)
            session["council_state"] = {
                "claims": claims,
                "disagreements": [],
                "revisions": [],
                "uncertainties": uncertainties,
                "role_assignments": analyst_assignments,
                "verification_summary": {},
            }
            session["current_state"]["status"] = "single_model_test_completed"
            session["current_state"]["round_status"] = "completed"
            self._checkpoint(session)
            if renderer:
                first_model = analyst_assignments[0]["assigned_model"]
                parsed_out = round_one_structured.get(first_model, {})
                self._render(
                    renderer.render_single_model_test_summary(
                        analyst_assignments,
                        parsed_out,
                        session["council_state"].get("claims", []),
                        parsed_out.get("assumptions", []),
                        parsed_out.get("uncertainties", []),
                    )
                )
            return True, session, analyst_assignments

        role_plan = session["role_plan"]
        health_report = session["health_report"]
        analyst_ids = role_plan.get("analyst", []) or [model["id"] for model in healthy_models]
        analyst_assignments = self._select_role_models("analyst", analyst_ids, healthy_models, allow_multiple=True)
        analyst_assignments, analyst_preflight = self._validate_round_assignments(
            1,
            "analyst",
            analyst_assignments,
            models,
            health_report,
            allow_multiple=True,
            renderer=renderer,
        )
        analyst_prompt = self._build_round_one_prompt(session, question, healthy_models)
        if renderer:
            self._render(renderer.render_round_header(1, "Independent analysis", "analyst"))
        round_one_outputs = self._run_assignments(analyst_assignments, lambda assignment: analyst_prompt)
        round_one_structured = {}
        for assignment in analyst_assignments:
            model_id = assignment["assigned_model"]
            result = round_one_outputs.get(model_id, {})
            if result.get("error"):
                continue
            parsed = parse_round_one(result.get("response", ""))
            contribution_status = contribution_status_for("analyst", result.get("response", ""), parsed, done_reason=result.get("done_reason"))
            result["parsed"] = parsed
            result["contribution_status"] = contribution_status
            assignment["contribution_status"] = contribution_status
            round_one_structured[model_id] = parsed
            if renderer:
                self._render(renderer.render_model_result(model_id, "analyst", {
                    "status": assignment["execution_status"],
                    "contribution_status": contribution_status,
                    "position": parsed.get("position", ""),
                    "claims": parsed.get("claims", []),
                    "assumptions": parsed.get("assumptions", []),
                    "uncertainties": parsed.get("uncertainties", []),
                    "confidence": parsed.get("confidence"),
                }))
        round_status = self._round_result_status(analyst_assignments)
        self._record_round(session, 1, "analyst", analyst_assignments, round_one_outputs, round_one_structured, round_status)
        claims, assumptions, uncertainties, model_to_claim_ids = self._build_state_from_round_one(round_one_outputs)
        information_gain = self._compute_information_gain(round_one_outputs, [])
        council_state = {
            "claims": claims,
            "disagreements": [],
            "assumptions": assumptions,
            "uncertainties": uncertainties,
            "revisions": [],
            "role_assignments": [self._assignment_display(a) for a in analyst_assignments],
            "verification_summary": self._verification_summary(claims, []),
            "model_independence": self._model_independence_audit([self._assignment_display(a) for a in analyst_assignments]),
            "information_gain": information_gain,
        }
        session["council_state"] = council_state
        session["current_state"]["claims"] = claims
        session["current_state"]["assumptions"] = assumptions
        session["current_state"]["uncertainties"] = uncertainties
        session["current_state"]["role_assignments"] = council_state["role_assignments"]
        session["current_state"]["council_state"] = self._compact_council_state(council_state)
        self._checkpoint(session)
        if progress_hook:
            progress_hook(1, session)

        valid_analyst_contribs = [
            a for a in analyst_assignments
            if a.get("contribution_status") == "valid"
        ]
        if len(claims) == 0 or len(valid_analyst_contribs) == 0:
            session["current_state"]["status"] = "INSUFFICIENT_EVIDENCE"
            session["current_state"]["session_status"] = "incomplete"
            session["current_state"]["epistemic_status"] = "insufficient_evidence"
            session["current_state"]["round_status"] = "blocked"
            if renderer:
                self._render("\n[INSUFFICIENT_EVIDENCE] Round 1 yielded zero valid claims/contributions. Deliberation paused.\n")
            session["telemetry"]["session_end_timestamp"] = now()
            session["telemetry"]["total_latency_ms"] = round((time.perf_counter() - session_start_perf) * 1000)
            self._checkpoint(session)
            return False, session, analyst_assignments

        return True, session, analyst_assignments

    def _run_phase_2(self, session, question, healthy_models, models, renderer, progress_hook, role_plan, health_report, analyst_assignments, session_start_perf):
        import time
        comparator_id = (role_plan.get("comparator") or [None])[0]
        comparator_assignments = self._select_role_models("comparator", [comparator_id] if comparator_id else [], healthy_models, allow_multiple=False)
        comparator_assignments, comparator_preflight = self._validate_round_assignments(
            2,
            "comparator",
            comparator_assignments,
            models,
            health_report,
            allow_multiple=False,
            renderer=renderer,
        )
        if not comparator_assignments:
            raise InsufficientHealthyModels("No comparator model could be assigned")
        comparator_prompt = self._build_round_two_prompt(session, question)
        if renderer:
            self._render(renderer.render_round_header(2, "Structured comparison", "comparator"))
        comparator_outputs = self._run_assignments(comparator_assignments, lambda assignment: comparator_prompt)
        comparator_result = next(iter(comparator_outputs.values()))
        parsed_two = parse_round_two(comparator_result.get("response", ""))
        comparator_status = contribution_status_for("comparator", comparator_result.get("response", ""), parsed_two, done_reason=comparator_result.get("done_reason"))
        comparator_result["contribution_status"] = comparator_status
        comparator_assignments[0]["contribution_status"] = comparator_status
        known_participant_ids = {m["id"] for m in models} if isinstance(models, list) else set(models)
        
        claims = session["council_state"]["claims"]
        assumptions = session["council_state"]["assumptions"]
        uncertainties = session["council_state"]["uncertainties"]

        # Only materialize comparison state if contribution is usable (valid or partial with content)
        if comparator_status not in ("empty", "malformed"):
            claims = self._apply_claim_assessments(claims, parsed_two.get("claim_assessments", []), known_participant_ids=known_participant_ids)
            disagreements = self._materialize_disagreements(parsed_two.get("disagreements", []), claims, known_participant_ids=known_participant_ids)
            assumptions = self._merge_assumptions(assumptions, parsed_two.get("assumptions", []))
            uncertainties = self._merge_uncertainties(uncertainties, parsed_two.get("uncertainties", []))
        else:
            disagreements = []

        # If single analyst position, explicitly record that cross-model disagreement cannot be established
        single_analyst_run = len(set(a.get("actual_model") for a in analyst_assignments)) <= 1
        if single_analyst_run:
            cross_model_notice = "Single analyst participant available; cross-model disagreement cannot be established."
        else:
            cross_model_notice = None

        council_state = {
            "claims": claims,
            "disagreements": disagreements,
            "assumptions": assumptions,
            "uncertainties": uncertainties,
            "revisions": session.get("council_state", {}).get("revisions", []),
            "role_assignments": session["council_state"]["role_assignments"] + [self._assignment_display(a) for a in comparator_assignments],
            "verification_summary": self._verification_summary(claims, disagreements),
            "cross_model_notice": cross_model_notice,
        }
        session["council_state"] = council_state
        session["current_state"]["claims"] = claims
        session["current_state"]["disagreements"] = disagreements
        session["current_state"]["assumptions"] = assumptions
        session["current_state"]["uncertainties"] = uncertainties
        session["current_state"]["council_state"] = self._compact_council_state(council_state)
        
        if renderer:
            self._render(renderer.render_role_assignment_round(2, comparator_assignments))
            self._render(renderer.render_disagreements(disagreements))
        
        self._record_round(session, 2, "comparator", comparator_assignments, comparator_outputs, parsed_two, self._round_result_status(comparator_assignments))
        self._checkpoint(session)
        if progress_hook:
            progress_hook(2, session)

        if comparator_status != "valid":
            # If Round 2 is partial/empty/malformed/failed/timeout, subsequent rounds are BLOCKED
            session["current_state"]["status"] = "incomplete"
            session["current_state"]["session_status"] = "incomplete"
            session["current_state"]["epistemic_status"] = "partial_comparison" if comparator_status == "partial" else "insufficient_evidence"
            session["current_state"]["round_status"] = "blocked"
            if renderer:
                self._render(f"\n[BLOCKED] Round 2 comparison contribution status is {comparator_status}. Deliberation paused.\n")
            session["telemetry"]["session_end_timestamp"] = now()
            session["telemetry"]["total_latency_ms"] = round((time.perf_counter() - session_start_perf) * 1000)
            self._checkpoint(session)
            return False, session, comparator_assignments

        return True, session, comparator_assignments

    def _run_phase_3(self, session, question, healthy_models, models, renderer, progress_hook, role_plan, health_report, comparator_assignments, known_participant_ids):
        claims = session["council_state"]["claims"]
        disagreements = session["council_state"]["disagreements"]
        assumptions = session["council_state"]["assumptions"]
        uncertainties = session["council_state"]["uncertainties"]

        challenger_ids = role_plan.get("challenger", [])
        if len(disagreements) == 0 or role_plan.get("challenger") == []:
            challenger_assignments = []
            parsed_revisions = []
            challenger_outputs = {}
            reason = "No disagreements found in Round 2" if len(disagreements) == 0 else "No challenger models requested in role plan"
            self._record_round(
                session,
                3,
                "challenger",
                [],
                {},
                {"status": "skipped", "reason": reason},
                "skipped"
            )
            if renderer:
                self._render(f"\nRound 3: Targeted deliberation [challenger] skipped ({reason}).\n")
            session["council_state"]["revisions"] = []
            session["current_state"]["revisions"] = []
            session["current_state"]["council_state"] = self._compact_council_state(session["council_state"])
            self._checkpoint(session)
            if progress_hook:
                progress_hook(3, session)
        else:
            exclude_ids = {a["assigned_model"] for a in comparator_assignments}
            challenger_assignments = self._select_role_models("challenger", challenger_ids, healthy_models, allow_multiple=True, exclude_ids=exclude_ids)
            challenger_assignments, challenger_preflight = self._validate_round_assignments(
                3,
                "challenger",
                challenger_assignments,
                models,
                health_report,
                exclude_ids=exclude_ids,
                allow_multiple=True,
                renderer=renderer,
            )
            if not challenger_assignments:
                raise InsufficientHealthyModels("No challenger model could be assigned")
            challenger_prompt = self._build_round_three_prompt(session, question)
            if renderer:
                self._render(renderer.render_round_header(3, "Targeted deliberation", "challenger"))
            challenger_outputs = self._run_assignments(challenger_assignments, lambda assignment: challenger_prompt)
            parsed_revisions = []
            for model_id, result in challenger_outputs.items():
                if result.get("error"):
                    continue
                parsed = parse_round_three(result.get("response", ""))
                contribution_status = contribution_status_for("challenger", result.get("response", ""), parsed, done_reason=result.get("done_reason"))
                result["parsed"] = parsed
                result["contribution_status"] = contribution_status
                for assignment in challenger_assignments:
                    if assignment["assigned_model"] == model_id:
                        assignment["contribution_status"] = contribution_status
                parsed_revisions.extend(self._materialize_revisions(parsed.get("revisions", []), known_participant_ids=known_participant_ids))
                if renderer:
                    self._render(renderer.render_model_result(model_id, "challenger", {
                        "status": "completed",
                        "contribution_status": contribution_status,
                        "revisions": [item.get("revised_position", "") for item in parsed_revisions if item.get("model") == model_id],
                        "verification_status": "unverified",
                    }))
            council_state = {
                "claims": claims,
                "disagreements": disagreements,
                "assumptions": assumptions,
                "uncertainties": uncertainties,
                "revisions": parsed_revisions,
                "role_assignments": session["council_state"]["role_assignments"] + [self._assignment_display(a) for a in challenger_assignments],
                "verification_summary": self._verification_summary(claims, disagreements),
            }
            session["council_state"] = council_state
            session["current_state"]["revisions"] = parsed_revisions
            session["current_state"]["council_state"] = self._compact_council_state(council_state)
            if renderer:
                self._render(renderer.render_role_assignment_round(3, challenger_assignments))
                self._render(renderer.render_revisions(parsed_revisions))
            self._record_round(session, 3, "challenger", challenger_assignments, challenger_outputs, parsed_revisions, self._round_result_status(challenger_assignments))
            self._checkpoint(session)
            if progress_hook:
                progress_hook(3, session)
        return session, challenger_assignments, parsed_revisions

    def _run_phase_4(self, session, question, healthy_models, models, renderer, progress_hook, role_plan, health_report, challenger_assignments, parsed_revisions, session_start_perf):
        import time
        claims = session["council_state"]["claims"]
        disagreements = session["council_state"]["disagreements"]
        assumptions = session["council_state"]["assumptions"]
        uncertainties = session["council_state"]["uncertainties"]

        if len(claims) == 0:
            session["current_state"]["status"] = "incomplete"
            session["current_state"]["session_status"] = "incomplete"
            session["current_state"]["epistemic_status"] = "insufficient_evidence"
            session["current_state"]["round_status"] = "blocked"
            if renderer:
                self._render("\n[INSUFFICIENT_EVIDENCE] Cannot perform synthesis with zero validated claims. Deliberation paused.\n")
            session["telemetry"]["session_end_timestamp"] = now()
            session["telemetry"]["total_latency_ms"] = round((time.perf_counter() - session_start_perf) * 1000)
            self._checkpoint(session)
            return session

        synthesizer_id = (role_plan.get("synthesizer") or [None])[0]
        exclude_ids = {a["assigned_model"] for a in challenger_assignments}
        synthesizer_assignments = self._select_role_models("synthesizer", [synthesizer_id] if synthesizer_id else [], healthy_models, allow_multiple=False, exclude_ids=exclude_ids)
        synthesizer_assignments, synthesizer_preflight = self._validate_round_assignments(
            4,
            "synthesizer",
            synthesizer_assignments,
            models,
            health_report,
            exclude_ids=exclude_ids,
            allow_multiple=False,
            renderer=renderer,
        )
        if not synthesizer_assignments:
            raise InsufficientHealthyModels("No synthesizer model could be assigned")
        synthesis_prompt = self._build_round_four_prompt(session, question)
        if renderer:
            self._render(renderer.render_round_header(4, "Synthesis", "synthesizer"))
        synthesizer_outputs = self._run_assignments(synthesizer_assignments, lambda assignment: synthesis_prompt)
        synthesizer_result = next(iter(synthesizer_outputs.values()))
        parsed_four = parse_round_four(synthesizer_result.get("response", ""))
        synthesizer_status = contribution_status_for("synthesizer", synthesizer_result.get("response", ""), parsed_four, done_reason=synthesizer_result.get("done_reason"))
        synthesizer_result["contribution_status"] = synthesizer_status
        synthesizer_assignments[0]["contribution_status"] = synthesizer_status
        all_role_assignments = session["council_state"]["role_assignments"] + [self._assignment_display(a) for a in synthesizer_assignments]
        council_state = {
            "claims": claims,
            "disagreements": disagreements,
            "assumptions": assumptions,
            "uncertainties": uncertainties,
            "revisions": parsed_revisions,
            "role_assignments": all_role_assignments,
            "verification_summary": self._verification_summary(claims, disagreements),
            "model_independence": self._model_independence_audit(all_role_assignments),
            "information_gain": session.get("council_state", {}).get("information_gain", {}),
        }
        session["council_state"] = council_state
        session["current_state"]["final_answer"] = parsed_four
        session["current_state"]["council_state"] = self._compact_council_state(session["council_state"])
        if renderer:
            self._render(renderer.render_role_assignment_round(4, synthesizer_assignments))
            self._render(renderer.render_final_decision(parsed_four))
        self._record_round(session, 4, "synthesizer", synthesizer_assignments, synthesizer_outputs, parsed_four, self._round_result_status(synthesizer_assignments))
        session["messages"].append({
            "id": str(uuid.uuid4()),
            "role": "council",
            "content": synthesizer_result.get("response", ""),
            "created_at": now(),
        })

        if synthesizer_status == "valid":
            session["current_state"]["status"] = "completed"
            session["current_state"]["session_status"] = "completed"
            session["current_state"]["epistemic_status"] = "sufficient_evidence"
        else:
            session["current_state"]["status"] = "incomplete"
            session["current_state"]["session_status"] = "incomplete"
            session["current_state"]["epistemic_status"] = "partial_synthesis"

        session["telemetry"]["session_end_timestamp"] = now()
        session["telemetry"]["total_latency_ms"] = round((time.perf_counter() - session_start_perf) * 1000)

        self._checkpoint(session)
        if progress_hook:
            progress_hook(4, session)

        if renderer:
            self._render(f"Session saved: {session['session_id']}")
        return session

    def _preflight_setup(self, session, question, models, role_plan, renderer, progress_hook, single_model_test):
        import time
        session_start_perf = time.perf_counter()
        session["telemetry"] = session.get("telemetry", {})
        session["telemetry"]["session_start_timestamp"] = now()
        
        renderer = renderer or self.renderer
        known_participant_ids = {m["id"] for m in models} if isinstance(models, list) else set(models)
        
        # Register active models in runtime registry
        self._model_registry[session["session_id"]] = {}
        for m in models:
            m_id = m["id"]
            if m_id in self._model_registry[session["session_id"]]:
                raise ValueError(f"Model ID collision: {m_id} is registered more than once in session {session['session_id']}")
            self._model_registry[session["session_id"]][m_id] = m

        self._ensure_state(session)
        session["messages"].append({
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": question,
            "created_at": now(),
        })
        session["current_state"]["last_question"] = question
        session["current_state"]["council_state_snapshot"] = {}
        session["current_state"]["single_model_test"] = single_model_test

        health_report, healthy_models = self.health_check_selected_models(models)
        session["health_report"] = health_report
        session["current_state"]["health_report"] = health_report
        session["current_state"]["healthy_models"] = [model["id"] for model in healthy_models]
        if renderer:
            if single_model_test:
                self._render(renderer.render_single_model_test_banner())
            self._render(renderer.render_selected_models(models, role_plan, health_report))
            self._render(renderer.render_health_report(health_report))
        self._checkpoint(session)
        if progress_hook:
            progress_hook(0, session)

        min_healthy = 1 if single_model_test else self.config.get("min_healthy_models", 2)
        if len(healthy_models) < min_healthy:
            session["current_state"]["status"] = "insufficient_healthy_models"
            self._checkpoint(session)
            raise InsufficientHealthyModels(f"Need at least {min_healthy} READY models, found {len(healthy_models)}")
            
        return session_start_perf, known_participant_ids, health_report, healthy_models

    def run(self, session, question, models, role_plan=None, renderer=None, progress_hook=None, single_model_test=False):
        session_start_perf, known_participant_ids, health_report, healthy_models = self._preflight_setup(
            session, question, models, role_plan, renderer, progress_hook, single_model_test
        )

        if not single_model_test:
            role_plan = self._health_filtered_role_plan(role_plan, models, healthy_models, health_report)
            session["role_plan"] = role_plan
            session["approved_role_plan"] = role_plan
            session["current_state"]["role_plan"] = role_plan
            self._checkpoint(session)

        success, session, analyst_assignments = self._run_phase_1(
            session,
            question,
            healthy_models,
            models,
            renderer,
            progress_hook,
            single_model_test,
            session_start_perf,
        )
        if single_model_test or not success:
            return session

        success, session, comparator_assignments = self._run_phase_2(
            session,
            question,
            healthy_models,
            models,
            renderer,
            progress_hook,
            role_plan,
            health_report,
            analyst_assignments,
            session_start_perf,
        )
        if not success:
            return session

        claims = session["council_state"]["claims"]
        disagreements = session["council_state"]["disagreements"]
        assumptions = session["council_state"]["assumptions"]
        uncertainties = session["council_state"]["uncertainties"]

        session, challenger_assignments, parsed_revisions = self._run_phase_3(
            session,
            question,
            healthy_models,
            models,
            renderer,
            progress_hook,
            role_plan,
            health_report,
            comparator_assignments,
            known_participant_ids,
        )

        return self._run_phase_4(
            session,
            question,
            healthy_models,
            models,
            renderer,
            progress_hook,
            role_plan,
            health_report,
            challenger_assignments,
            parsed_revisions,
            session_start_perf,
        )

    def _merge_assumptions(self, existing, new_items):
        merged = [dict(item) for item in existing]
        for item in new_items or []:
            if not isinstance(item, dict):
                item = {"assumption": str(item)}
            assumption_text = item.get("assumption") or item.get("text") or ""
            source_models = _coerce_id_list(item.get("source_models") or item.get("source"))
            status = str(item.get("status", "unverified")).strip().lower()
            if status not in {"unverified", "supported", "contradicted", "unresolved"}:
                status = "unverified"
            merged.append({
                "assumption_id": item.get("assumption_id") or self._new_id("A", len(merged) + 1),
                "assumption": assumption_text,
                "source_models": source_models,
                "status": status,
            })
        return merged

    def _merge_uncertainties(self, existing, new_items):
        merged = [dict(item) for item in existing]
        for item in new_items or []:
            if not isinstance(item, dict):
                item = {"uncertainty": str(item)}
            uncertainty_text = item.get("uncertainty") or item.get("text") or ""
            source_models = _coerce_id_list(item.get("source_models") or item.get("source"))
            affected_claims = _coerce_id_list(item.get("affected_claims") or item.get("claims"))
            status = str(item.get("status", "open")).strip().lower()
            if status not in {"open", "resolved", "unresolved"}:
                status = "open"
            merged.append({
                "uncertainty_id": item.get("uncertainty_id") or self._new_id("U", len(merged) + 1),
                "uncertainty": uncertainty_text,
                "affected_claims": affected_claims,
                "source_models": source_models,
                "status": status,
            })
        return merged

    def _build_round_one_prompt(self, session, question, healthy_models):
        task = {
            "task_id": session["session_id"],
            "question": question,
            "objective": "Answer the user accurately and identify meaningful uncertainty.",
            "constraints": [
                "Do not treat another model's claim as fact without support.",
                "Explicitly identify disagreement.",
                "Prefer correction over forced consensus.",
            ],
        }
        return f"""{SYSTEM}

CANONICAL TASK:
{json.dumps(task, ensure_ascii=False, indent=2)}

USER QUESTION:
{question}

You are participating as an analyst. Return your analysis using either:
Option A (JSON):
{{"position":"one sentence","claims":["claim1","claim2"],"assumptions":["assumption1"],"uncertainties":["uncertainty1"],"confidence":0.8}}

Option B (HEADINGS):
Position: one sentence
Claims:
- claim text
Assumptions:
- assumption text
Uncertainties:
- uncertainty text
Confidence: number between 0 and 1
""".strip()

    def _build_council_state_prompt_block(self, session):
        return compact_json(self._compact_council_state(session.get("council_state", {})), self.config.get("context_budget_chars", 12000))

    def _build_round_two_prompt(self, session, question):
        return f"""{SYSTEM}

CANONICAL TASK
{json.dumps({"question": question, "task_id": session["session_id"]}, ensure_ascii=False, indent=2)}

COUNCIL STATE
{self._build_council_state_prompt_block(session)}

Return this contract. JSON is preferred, but the headings are acceptable.
Claim assessments:
- claim_id=C-001 | verification_status=supported | supporting_models=model-a,model-b | contradicting_models=model-c | confidence=0.8
Disagreements:
- disagreement_id=D-001 | claim_id=C-001 | claim=claim text | model_positions=model-a: position; model-b: position | status=open | resolution=unresolved
Assumptions:
- assumption_id=A-001 | assumption=text | source_models=model-a | status=unverified
Uncertainties:
- uncertainty_id=U-001 | uncertainty=text | affected_claims=C-001 | source_models=model-a | status=open

Do not treat agreement as verification.
Only record disagreements for material differences in position, recommendation, assumption, or interpretation.
""".strip()

    def _build_round_three_prompt(self, session, question):
        return f"""{SYSTEM}

CANONICAL TASK
{json.dumps({"question": question, "task_id": session["session_id"]}, ensure_ascii=False, indent=2)}

COUNCIL STATE
{self._build_council_state_prompt_block(session)}

Return this contract. JSON is preferred, but the headings are acceptable.
Revisions:
- revision_id=R-001 | model=model-id | original_position=original text | revised_position=revised text | reason=reason text | affected_claims=C-001

Only include revisions when the model position actually changed.
""".strip()

    def _build_round_four_prompt(self, session, question):
        return f"""{SYSTEM}

CANONICAL TASK
{json.dumps({"question": question, "task_id": session["session_id"]}, ensure_ascii=False, indent=2)}

COUNCIL STATE
{self._build_council_state_prompt_block(session)}

Return this contract. JSON is preferred, but headings are acceptable.
Decision: one clear conclusion
Rationale: concise explanation
Conditions:
- condition text
Trade-offs:
- trade-off text
Unresolved Issues:
- unresolved issue text
Confidence: number between 0 and 1

Do not turn unverified claims into verified facts.
""".strip()

    def run_benchmark(self, path: Path, models):
        with open(path, "r", encoding="utf-8") as f:
            cases = [json.loads(line) for line in f if line.strip()]

        results = []
        print(f"\nRunning {len(cases)} benchmark cases...\n")

        for idx, case in enumerate(cases, 1):
            question = case["question"]
            print(f"[{idx}/{len(cases)}] {question[:90]}")
            row = {"id": case.get("id", str(idx)), "question": question, "models": {}}

            for m in models:
                r = self._call(
                    m,
                    f"""{SYSTEM}

Answer the benchmark question. Be concise and precise.

QUESTION:
{question}
""",
                )
                row["models"][m["id"]] = r

            results.append(row)

        out = path.with_name(path.stem + "_results.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"\n✓ Raw benchmark results: {out}")
        print("Automatic accuracy scoring is intentionally not applied unless the dataset supplies reference answers.")


