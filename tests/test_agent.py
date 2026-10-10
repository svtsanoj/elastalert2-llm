import json

from elastalert_llm.agent import Agent, ToolBox
from elastalert_llm.agent.loop import parse_text_tool_call
from elastalert_llm.llm import ChatResult


class FakeBackend:
    """Replays scripted ChatResults and records the messages it was sent."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def chat(self, model, messages, tools=None, options=None, on_token=None, stop_event=None):
        self.calls.append([dict(m) for m in messages])
        result = self.results.pop(0)
        if on_token and result.content:
            on_token(result.content)
        return result


def write_rule(tmp_path, name="r.yaml", text="name: r\ntype: any\nindex: a\nalert: debug\n"):
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_tool_call_then_answer(tmp_path):
    write_rule(tmp_path)
    backend = FakeBackend(
        ChatResult(tool_calls=[{"function": {"name": "list_rules", "arguments": {}}}]),
        ChatResult(content="You have one rule."),
    )
    events = []
    answer = Agent(backend, "m", ToolBox(tmp_path)).run("what rules?", emit=lambda k, d: events.append(k))

    assert answer == "You have one rule."
    tool_message = backend.calls[1][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_name"] == "list_rules"
    assert json.loads(tool_message["content"])["rules"][0]["valid"] is True
    assert events.count("tool_call") == 1 and events[-1] == "done"


def test_text_tool_call_fallback(tmp_path):
    write_rule(tmp_path)
    backend = FakeBackend(
        ChatResult(content='```json\n{"name": "read_rule", "arguments": {"path": "r.yaml"}}\n```'),
        ChatResult(content="It matches everything."),
    )
    events = []
    Agent(backend, "m", ToolBox(tmp_path)).run("explain r.yaml", emit=lambda k, d: events.append(k))
    assert "retract" in events
    assistant = backend.calls[1][-2]
    assert assistant["content"] == "" and assistant["tool_calls"][0]["function"]["name"] == "read_rule"


def test_max_steps(tmp_path):
    loop_call = ChatResult(tool_calls=[{"function": {"name": "list_rules", "arguments": {}}}])
    backend = FakeBackend(*[loop_call] * 3)
    answer = Agent(backend, "m", ToolBox(tmp_path), max_steps=3).run("go")
    assert "Stopped after 3" in answer


def test_parse_text_tool_call():
    known = {"list_rules": None}
    assert parse_text_tool_call('<tool_call>\n{"name": "list_rules", "arguments": {}}\n</tool_call>', known) == \
        {"name": "list_rules", "arguments": {}}
    assert parse_text_tool_call('{"name": "rm_rf", "arguments": {}}', known) is None
    assert parse_text_tool_call("name: my rule\ntype: any", known) is None


def test_read_rule_stays_inside_rules_folder(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (tmp_path / "secret.yaml").write_text("x: 1", encoding="utf-8")
    result = json.loads(ToolBox(rules).call("read_rule", {"path": "../secret.yaml"}))
    assert "outside" in result["error"]


def test_es_tools_without_cluster(tmp_path):
    result = json.loads(ToolBox(tmp_path).call("list_indices", {}))
    assert "not configured" in result["error"]
