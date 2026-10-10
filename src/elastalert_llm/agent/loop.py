"""The agent loop: ask the model, run the tools it calls, repeat until it answers in text."""
import json
import re

from elastalert_llm.agent.prompts import build_system_prompt

# Small models sometimes write a tool call as text instead of using the tools API.
_TEXT_CALL = re.compile(r"^\s*(?:<tool_call>|```(?:json)?)?\s*(\{.*\})\s*(?:</tool_call>|```)?\s*$", re.DOTALL)


class Agent:
    """Holds one conversation. run() is blocking; call it from a worker thread in a GUI.

    emit(kind, data) receives progress events:
      "assistant_start" None, "token" str, "retract" None (discard streamed text: it was a tool call),
      "tool_call" {"name", "arguments"}, "tool_result" {"name", "result"},
      "stats" ChatResult, "done" str (final answer), "error" str
    """

    def __init__(self, backend, model, toolbox, max_steps=8, options=None, use_tools=True):
        self.backend = backend
        self.model = model
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.options = options or {}
        self.use_tools = use_tools
        self.messages = []
        self.reset()

    def reset(self):
        self.messages = [{"role": "system", "content": build_system_prompt()}]

    def run(self, user_text, emit=lambda kind, data: None, stop_event=None):
        self.messages.append({"role": "user", "content": user_text})
        tools = self.toolbox.schemas() if self.use_tools else None

        for _ in range(self.max_steps):
            emit("assistant_start", None)
            result = self.backend.chat(self.model, self.messages, tools=tools, options=self.options,
                                       on_token=lambda t: emit("token", t), stop_event=stop_event)
            emit("stats", result)
            if result.stopped:
                self.messages.append({"role": "assistant", "content": result.content})
                emit("error", "Stopped.")
                return result.content

            calls = [_normalise(c) for c in result.tool_calls]
            content = result.content
            if not calls and self.use_tools:
                text_call = parse_text_tool_call(content, self.toolbox.tools)
                if text_call:
                    calls, content = [text_call], ""
                    emit("retract", None)

            message = {"role": "assistant", "content": content}
            if calls:
                message["tool_calls"] = [{"function": c} for c in calls]
            self.messages.append(message)

            if not calls:
                emit("done", content)
                return content

            for call in calls:
                emit("tool_call", call)
                output = self.toolbox.call(call["name"], call["arguments"])
                emit("tool_result", {"name": call["name"], "result": output})
                self.messages.append({"role": "tool", "tool_name": call["name"], "content": output})

        message = f"Stopped after {self.max_steps} tool steps without a final answer. Ask me to continue, or rephrase."
        emit("error", message)
        return message


def _normalise(call):
    function = call.get("function", call)
    arguments = function.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    return {"name": function.get("name", ""), "arguments": arguments}


def parse_text_tool_call(content, known_tools):
    """Return {"name", "arguments"} if the whole reply is a JSON tool call for a known tool, else None."""
    match = _TEXT_CALL.match(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("name") not in known_tools:
        return None
    return _normalise({"function": {"name": data["name"], "arguments": data.get("arguments", data.get("parameters", {}))}})
