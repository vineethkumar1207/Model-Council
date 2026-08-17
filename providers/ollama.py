import json
import os
import socket
import urllib.request
import urllib.error
from pathlib import Path


def _resolve_env_api_key():
    key = os.environ.get("OLLAMA_API_KEY")
    if key:
        return key.strip()
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OLLAMA_API_KEY=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            pass
    return None


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url, api_key=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or _resolve_env_api_key()

    def _request(self, path, payload=None, timeout=300):
        data = None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            message = body.strip() or str(exc)
            raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unavailable: {exc.reason}") from exc
        except socket.timeout as exc:
            raise TimeoutError("Request timed out") from exc

    def list_models(self):
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/api/tags", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unavailable: {exc.reason}") from exc
        result = []
        for m in data.get("models", []):
            result.append({
                "id": m.get("name"),
                "provider": "ollama",
                "size": f"{round(m.get('size', 0) / (1024**3), 2)} GB"
            })
        return result

    def has_model(self, model_id):
        return any(x["id"] == model_id for x in self.list_models())

    def generate(self, model, prompt, max_output_tokens=500, temperature=0.2, timeout_seconds=300, num_ctx=None):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_output_tokens
            }
        }
        if num_ctx is not None:
            payload["options"]["num_ctx"] = num_ctx
        data = self._request("/api/chat", payload, timeout=timeout_seconds)
        message = data.get("message", {}) if isinstance(data, dict) else {}
        response = message.get("content", "") if isinstance(message, dict) else ""
        if not (response and response.strip()):
            if isinstance(message, dict):
                response = (
                    message.get("thinking", "")
                    or message.get("reasoning_content", "")
                    or message.get("thinking_content", "")
                )
            if not (response and response.strip()) and isinstance(data, dict):
                response = data.get("response", "") or data.get("thinking", "")
        # Build latency_detail from Ollama's native timing fields (Phase Y measurement)
        ns_to_ms = lambda ns: round(ns / 1e6, 1) if isinstance(ns, (int, float)) else None
        eval_count = data.get("eval_count") if isinstance(data, dict) else None
        eval_dur_ns = data.get("eval_duration") if isinstance(data, dict) else None
        tokens_per_second = None
        if eval_count and eval_dur_ns and eval_dur_ns > 0:
            tokens_per_second = round(eval_count / (eval_dur_ns / 1e9), 1)
        latency_detail = {
            "model_load_ms": ns_to_ms(data.get("load_duration") if isinstance(data, dict) else None),
            "prompt_eval_ms": ns_to_ms(data.get("prompt_eval_duration") if isinstance(data, dict) else None),
            "generation_ms": ns_to_ms(eval_dur_ns),
            "total_ms": ns_to_ms(data.get("total_duration") if isinstance(data, dict) else None),
            "tokens_per_second": tokens_per_second,
        }
        return {
            "response": (response or "").strip(),
            "input_tokens": data.get("prompt_eval_count") if isinstance(data, dict) else None,
            "output_tokens": data.get("eval_count") if isinstance(data, dict) else None,
            "done": data.get("done", True) if isinstance(data, dict) else True,
            "done_reason": data.get("done_reason") if isinstance(data, dict) else None,
            "latency_detail": latency_detail,
        }

    def health_check(self, model, max_output_tokens=1, temperature=0.0, timeout_seconds=20, num_ctx=None):
        prompt = "Health check. Reply with READY."
        return self.generate(
            model,
            prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            num_ctx=num_ctx
        )
