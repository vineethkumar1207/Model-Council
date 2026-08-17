import time
from parsers import now

class ModelExecutor:
    def __init__(self, providers, config):
        self.providers = providers
        self.config = config

    def provider_for_model(self, model_id):
        for name, provider in self.providers.items():
            try:
                if provider.has_model(model_id):
                    return name
            except Exception:
                continue
        return next(iter(self.providers.keys()), None)

    def _provider_for_model_entry(self, model):
        provider_name = model.get("provider")
        if provider_name and provider_name in self.providers:
            return provider_name
        return self.provider_for_model(model["id"])

    def execute(self, model, prompt, timeout_seconds=None, role=None):
        model_id = model["id"]
        provider_name = self._provider_for_model_entry(model)
        if provider_name not in self.providers:
            raise RuntimeError(f"Unavailable: no provider for model {model_id}")
        provider = self.providers[provider_name]

        # Role-specific output token budgets
        budget_map = {
            "analyst": 1500,
            "comparator": 3000,
            "challenger": 1500,
            "synthesizer": 2000,
        }
        role_budget = budget_map.get(role) if role else None
        max_tokens = self.config.get(f"{role}_output_tokens") if role else None
        max_tokens = max_tokens or role_budget or self.config.get("max_output_tokens", 500)

        model_profile = self.config.get("model_profiles", {}).get(model_id, {})
        
        # Override output token budget
        model_max_tokens = model_profile.get("max_output_tokens") or model_profile.get("max_tokens")
        if model_max_tokens is not None:
            max_tokens = model_max_tokens

        # Override temperature
        temperature = model_profile.get("temperature")
        if temperature is None:
            temperature = self.config.get("temperature", 0.2)

        # Override timeout
        model_timeout = model_profile.get("timeout_seconds") or model_profile.get("timeout")
        if timeout_seconds is None and model_timeout is not None:
            timeout_seconds = model_timeout

        # Override num_ctx
        num_ctx = model_profile.get("num_ctx")
        if num_ctx is None:
            num_ctx = self.config.get("num_ctx")

        kwargs = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if num_ctx is not None:
            kwargs["num_ctx"] = num_ctx

        request_start = now()
        start = time.perf_counter()
        result = provider.generate(model_id, prompt, **kwargs)
        response_completion = now()

        result["latency_ms"] = round((time.perf_counter() - start) * 1000)
        result["request_start_timestamp"] = request_start
        result["response_completion_timestamp"] = response_completion
        result["model"] = model_id
        result["provider"] = provider_name
        result["status"] = "READY"
        return result

    def execute_health_check(self, model, timeout_seconds=20):
        provider_name = self._provider_for_model_entry(model)
        if provider_name not in self.providers:
            raise RuntimeError(f"Unavailable: no provider for model {model['id']}")
        provider = self.providers[provider_name]

        model_profile = self.config.get("model_profiles", {}).get(model["id"], {})
        num_ctx = model_profile.get("num_ctx")
        if num_ctx is None:
            num_ctx = self.config.get("num_ctx")

        kwargs = {"timeout_seconds": timeout_seconds}
        if num_ctx is not None:
            kwargs["num_ctx"] = num_ctx

        if hasattr(provider, "health_check"):
            return provider.health_check(model["id"], **kwargs)
        else:
            kwargs["max_output_tokens"] = 1
            kwargs["temperature"] = 0.0
            return provider.generate(
                model["id"],
                "Health check. Reply with READY.",
                **kwargs
            )
