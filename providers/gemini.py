class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key, configured_models=None):
        self.api_key = api_key
        self.configured_models = configured_models or []

    def _client(self):
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError(
                "Gemini support requires: pip install -r requirements.txt"
            ) from e
        return genai.Client(api_key=self.api_key)

    def list_models(self):
        return [
            {"id": model, "provider": "gemini", "size": "cloud"}
            for model in self.configured_models
        ]

    def has_model(self, model_id):
        return model_id in self.configured_models

    def generate(self, model, prompt, max_output_tokens=500, temperature=0.2, timeout_seconds=None):
        client = self._client()
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            },
        )
        usage = getattr(response, "usage_metadata", None)
        return {
            "response": getattr(response, "text", "") or "",
            "input_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
            "output_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
            "done": True
        }

    def health_check(self, model, max_output_tokens=1, temperature=0.0, timeout_seconds=None):
        return self.generate(
            model,
            "Health check. Reply with READY.",
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )
