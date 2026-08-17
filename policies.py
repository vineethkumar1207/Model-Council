from parsers import profile_for_model

class HealthPolicy:
    def __init__(self, config):
        self.config = config

    def classify_failure(self, exc):
        message = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in message:
            return "TIMEOUT"
        if "unavailable" in message or "connection refused" in message or "failed to establish" in message:
            return "UNAVAILABLE"
        if "not found" in message or "no such model" in message or "model not found" in message:
            return "UNAVAILABLE"
        if "out of memory" in message or "cuda" in message and "memory" in message:
            return "FAILED"
        if "http 500" in message:
            return "FAILED"
        return "FAILED"

    def execution_locality_for_model(self, model):
        if model.get("execution_locality"):
            return model["execution_locality"]
        model_id = model.get("id", "").lower()
        if model_id.endswith(":cloud") or "cloud" in model_id:
            return "cloud"
        return "local"

    def _resource_requirements_for_model(self, model):
        profile = self.config.get("model_profiles", {}).get(model["id"], {})
        return profile.get("resource_requirements", model.get("resource_requirements", {}))

    def violates_privacy(self, model, task_metadata):
        privacy = (task_metadata or {}).get("privacy_classification") or self.config.get("privacy_classification")
        if privacy in {"local_only", "private"} and self.execution_locality_for_model(model) == "cloud":
            return True
        return False

    def violates_resources(self, model):
        requirements = self._resource_requirements_for_model(model)
        max_memory_gb = self.config.get("max_model_memory_gb")
        if max_memory_gb is not None and requirements.get("memory_gb", 0) > max_memory_gb:
            return True
        return False


class RoleAllocator:
    def __init__(self, config, executor):
        self.config = config
        self.executor = executor

    def recommend_roles(self, models, health_report=None):
        eligible_models = self.ready_models(models, health_report)
        analysts = [m["id"] for m in eligible_models]
        comparator = self._best_model_for_role(eligible_models, "comparator")
        challengers = self._best_challengers(eligible_models, comparator)
        
        exclude_syn = set()
        if len(eligible_models) >= 3:
            if comparator:
                exclude_syn.add(comparator["id"])
            for c in challengers:
                exclude_syn.add(c["id"])
        
        synthesizer = self._best_model_for_role(eligible_models, "synthesizer", exclude_ids=exclude_syn)
        if not synthesizer and eligible_models:
            synthesizer = self._best_model_for_role(eligible_models, "synthesizer")

        recommendation = {
            "experimental": True,
            "reason": "Heuristic profile-based recommendation. Not scientifically validated.",
            "analyst": analysts,
            "comparator": [comparator["id"]] if comparator else [],
            "challenger": [m["id"] for m in challengers],
            "synthesizer": [synthesizer["id"]] if synthesizer else [],
            "profiles": {m["id"]: profile_for_model(m["id"], m.get("provider")) for m in eligible_models},
        }
        if health_report is not None:
            recommendation["excluded_models"] = self.health_exclusions(health_report)
        return recommendation

    def ready_models(self, models, health_report=None):
        if health_report is None:
            return list(models)
        return [
            model
            for model in models
            if health_report.get(model["id"], {}).get("status") == "READY"
        ]

    def health_exclusions(self, health_report):
        exclusions = []
        for model_id, info in health_report.items():
            status = info.get("status", "UNKNOWN")
            if status == "READY":
                continue
            exclusions.append({
                "model_id": model_id,
                "status": status,
                "reason": info.get("reason", ""),
                "provider": info.get("provider"),
            })
        return exclusions

    def _best_model_for_role(self, models, role, exclude_ids=None):
        exclude_ids = set(exclude_ids or [])
        candidates = [m for m in models if m["id"] not in exclude_ids]
        if not candidates:
            return None
        candidates.sort(key=lambda m: (self.role_score(m, role), m["id"]), reverse=True)
        return candidates[0]

    def _best_challengers(self, models, comparator):
        exclude = {comparator["id"]} if comparator else set()
        candidates = [m for m in models if m["id"] not in exclude]
        candidates.sort(key=lambda m: (self.role_score(m, "challenger"), m["id"]), reverse=True)
        return candidates[: max(1, min(2, len(candidates)))] if candidates else []

    def role_score(self, model, role):
        model_id = model["id"].lower()
        provider = (model.get("provider") or self.executor.provider_for_model(model["id"]) or "").lower()
        score = float(
            self.config.get("model_profiles", {})
            .get(model["id"], {})
            .get("role_capabilities", {})
            .get(role, 0.0)
        )
        if role == "analyst":
            score += 1
        if role == "comparator":
            score += 10 if "qwen" in model_id else 0
        if role == "challenger":
            score += 10 if "llama" in model_id else 0
        if role == "synthesizer":
            score += 10 if "gemini" in model_id or provider == "gemini" else 0
        if role in {"comparator", "challenger", "synthesizer"}:
            if role in profile_for_model(model["id"], provider):
                score += 5
        return score


class FallbackSelector:
    def __init__(self, health_policy, role_allocator):
        self.health_policy = health_policy
        self.role_allocator = role_allocator

    def fallback_model(self, role, healthy_models, exclude_ids=None):
        exclude_ids = set(exclude_ids or [])
        candidates = [m for m in healthy_models if m["id"] not in exclude_ids]
        if not candidates:
            return None
        candidates.sort(key=lambda m: (self.role_allocator.role_score(m, role), m["id"]), reverse=True)
        return candidates[0]

    def fallback_candidates(self, role, models, health_report, exclude_ids=None, task_metadata=None):
        exclude_ids = set(exclude_ids or [])
        ranked = []
        for model in models:
            model_id = model["id"]
            health = health_report.get(model_id, {}).get("status", "UNKNOWN")
            reasons = []
            if model_id in exclude_ids:
                reasons.append("explicitly excluded")
            if health != "READY":
                reasons.append(health)
            if self.health_policy.violates_privacy(model, task_metadata):
                reasons.append("privacy constraint")
            if self.health_policy.violates_resources(model):
                reasons.append("resource constraint")
            role_fitness = self.role_allocator.role_score(model, role)
            locality = self.health_policy.execution_locality_for_model(model)
            resource_fitness = 0.0 if self.health_policy.violates_resources(model) else 1.0
            runtime_fitness = 1.0 if health == "READY" else 0.0
            score = round((role_fitness * 0.7) + (resource_fitness * 0.2) + (runtime_fitness * 0.1), 4)
            ranked.append({
                "model": model_id,
                "eligible": not reasons,
                "score": score,
                "role_fitness": role_fitness,
                "resource_fitness": resource_fitness,
                "runtime_fitness": runtime_fitness,
                "health": health,
                "execution_locality": locality,
                "reasons": reasons,
            })
        ranked.sort(key=lambda item: (item["eligible"], item["score"], item["model"]), reverse=True)
        return ranked
