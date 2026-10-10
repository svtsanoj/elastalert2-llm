# elastalert2-llm

An agentic LLM assistant that helps people **create, debug, review and explain [ElastAlert 2](https://github.com/jertel/elastalert2) rules** without knowing the rule syntax. It runs against a local model through [Ollama](https://ollama.com), so nothing leaves your machine.

This is an independent add-on, not part of the official ElastAlert 2 project. Status: **early development (0.0.x)**.

## What works today

- A Tkinter desktop app: chat on the left, a rule YAML editor and a tool-call trace on the right.
- Ollama backend with streaming and tool calling. It falls back to parsing text tool calls, which small models such as `qwen2.5-coder:7b` often write.
- Agent tools:
  - Rules: `list_rules` (with validation status), `read_rule`, `validate_rule`, `rule_type_help`.
  - Elasticsearch (read-only): `list_indices`, `get_mapping`, `sample_docs`.
- A validator that uses ElastAlert 2's own rule loader (schema, required options, alerter set-up), plus hints for common gaps such as no `realert`, debug-only alerting, or placeholder values.

## Quick start (Windows)

```powershell
cd C:\Users\Sanoj\Projects\elastalert2-llm
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e ..\elastalert2   # or let pip fetch elastalert2 from PyPI
.venv\Scripts\python -m pip install -e ".[dev]"
ollama pull qwen2.5-coder:7b                             # any model with tool support works
.venv\Scripts\elastalert2-llm                            # or: .venv\Scripts\python -m elastalert_llm
```

The rules folder defaults to `examples/rules`. Two of its three rules are deliberately flawed, so you can try **Review** straight away.

To use the Elasticsearch tools, start the local stack in the fork's `_claude_docs/setup/es-local`, seed a scenario, and enter the ES URL, user and password in the toolbar. The password is never saved to disk; other settings are saved to `~/.elastalert2-llm.json`.

## Development

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m flake8 src tests
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
