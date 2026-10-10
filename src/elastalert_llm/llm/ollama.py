"""Ollama backend (local, the default). Uses /api/chat with streaming and tool calling."""
import json

import requests

from elastalert_llm.llm.base import ChatResult, LLMError

DEFAULT_URL = "http://localhost:11434"


class OllamaBackend:
    def __init__(self, base_url=DEFAULT_URL, timeout=900):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.timeout = timeout

    def list_models(self):
        """Names of the locally pulled models, e.g. ['qwen2.5-coder:7b']."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LLMError(f"Can't reach Ollama at {self.base_url}. Is it running? ({e})") from e
        return sorted(m["name"] for m in r.json().get("models", []))

    def chat(self, model, messages, tools=None, options=None, on_token=None, stop_event=None):
        """Stream one assistant turn and return it as a ChatResult.

        on_token(text) is called for each streamed piece of content.
        Setting stop_event ends the stream early (the result has stopped=True).
        """
        payload = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options

        result = ChatResult()
        parts = []
        try:
            with requests.post(f"{self.base_url}/api/chat", json=payload, stream=True, timeout=(10, self.timeout)) as r:
                if r.status_code != 200:
                    raise LLMError(f"Ollama returned HTTP {r.status_code}: {_error_text(r)}")
                for line in r.iter_lines():
                    if stop_event is not None and stop_event.is_set():
                        result.stopped = True
                        break
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise LLMError(f"Ollama error: {chunk['error']}")
                    message = chunk.get("message") or {}
                    text = message.get("content") or ""
                    if text:
                        parts.append(text)
                        if on_token:
                            on_token(text)
                    result.tool_calls.extend(message.get("tool_calls") or [])
                    if chunk.get("done"):
                        result.prompt_tokens = chunk.get("prompt_eval_count", 0)
                        result.output_tokens = chunk.get("eval_count", 0)
                        result.prompt_seconds = chunk.get("prompt_eval_duration", 0) / 1e9
                        result.output_seconds = chunk.get("eval_duration", 0) / 1e9
        except requests.RequestException as e:
            raise LLMError(f"Can't reach Ollama at {self.base_url}. Is it running? ({e})") from e
        result.content = "".join(parts)
        return result


def _error_text(response):
    try:
        return response.json().get("error", response.text)[:500]
    except ValueError:
        return response.text[:500]
