"""Types shared by all LLM backends."""
from dataclasses import dataclass, field


class LLMError(Exception):
    """The backend failed (unreachable, model missing, bad response)."""


@dataclass
class ChatResult:
    """One assistant turn.

    tool_calls use Ollama's shape: [{"function": {"name": str, "arguments": dict}}].
    """
    content: str = ""
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    output_tokens: int = 0
    prompt_seconds: float = 0.0
    output_seconds: float = 0.0
    stopped: bool = False

    @property
    def tokens_per_second(self):
        return self.output_tokens / self.output_seconds if self.output_seconds else 0.0
