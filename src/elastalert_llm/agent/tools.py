"""Tools the agent can call. All are read-only; results are JSON strings capped in size.

Rule tools work without Elasticsearch. ES tools return a clear error until a cluster is reachable.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from elastalert.loaders import RulesLoader

from elastalert_llm.es import ESClient, ESError, flatten_mapping
from elastalert_llm.verify import validate_rule_text

MAX_RESULT_CHARS = 4000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: callable

    def schema(self):
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _params(required=(), **props):
    return {"type": "object", "properties": props, "required": list(required)}


def _str(description):
    return {"type": "string", "description": description}


def _int(description):
    return {"type": "integer", "description": description}


class ToolBox:
    def __init__(self, rules_dir=None, es_client=None):
        self.rules_dir = Path(rules_dir).resolve() if rules_dir else None
        self.es = es_client
        self.tools = {t.name: t for t in self._build()}

    def schemas(self):
        return [t.schema() for t in self.tools.values()]

    def call(self, name, arguments):
        """Run a tool and return its result as a (truncated) JSON string. Never raises."""
        tool = self.tools.get(name)
        if tool is None:
            return _dump({"error": f"Unknown tool '{name}'. Available: {', '.join(self.tools)}"})
        try:
            result = tool.func(**(arguments or {}))
        except TypeError as e:
            result = {"error": f"Bad arguments for {name}: {e}"}
        except (ESError, OSError, ValueError) as e:
            result = {"error": str(e)}
        return _dump(result)

    def _build(self):
        return [
            Tool("list_rules", "List the rule files in the rules folder with each rule's name, type, index, alert, "
                 "and whether it is valid (with its errors).",
                 _params(), self.list_rules),
            Tool("read_rule", "Read the YAML of one rule file from the rules folder.",
                 _params(["path"], path=_str("Rule file path relative to the rules folder, as returned by list_rules")), self.read_rule),
            Tool("validate_rule", "Validate a rule with ElastAlert's own loader. Returns errors (must fix) and warnings (hints). "
                 "Pass yaml_text for a new rule, or path for an existing file. Always validate before showing a rule to the user.",
                 _params(yaml_text=_str("The complete rule YAML"), path=_str("Or: a rule file path from list_rules")),
                 self.validate_rule),
            Tool("rule_type_help", "Required options and description of an ElastAlert rule type, e.g. 'frequency', 'spike', 'flatline'.",
                 _params(["rule_type"], rule_type=_str("Rule type name")), self.rule_type_help),
            Tool("list_indices", "List Elasticsearch indices matching a pattern, with doc counts.",
                 _params(pattern=_str("Index pattern, default '*'")), self.list_indices),
            Tool("get_mapping", "Field names and types for an index or pattern. Use this before writing filters; never guess field names.",
                 _params(["index"], index=_str("Index name or pattern")), self.get_mapping),
            Tool("sample_docs", "Return a few recent documents from an index, optionally matching a Lucene query string.",
                 _params(["index"], index=_str("Index name or pattern"), query=_str("Optional Lucene query string, e.g. 'level:ERROR'"),
                         size=_int("Number of documents, 1-5"), timestamp_field=_str("Time field to sort by, default '@timestamp'")),
                 self.sample_docs),
        ]

    # --- rule tools -------------------------------------------------------

    def list_rules(self):
        if not self.rules_dir:
            return {"error": "No rules folder is configured."}
        rules = []
        for path in sorted(self.rules_dir.rglob("*.y*ml")):
            entry = {"path": path.relative_to(self.rules_dir).as_posix()}
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                entry["errors"] = [f"unreadable: {e}"]
            else:
                check = validate_rule_text(text, str(self.rules_dir))
                entry.update(check["summary"])
                entry["valid"] = check["ok"]
                if check["errors"]:
                    entry["errors"] = check["errors"]
            rules.append(entry)
        return {"rules_folder": str(self.rules_dir), "rules": rules}

    def read_rule(self, path):
        target = self._rule_path(path)
        return {"path": path, "yaml": target.read_text(encoding="utf-8")}

    def validate_rule(self, yaml_text=None, path=None):
        if path and not yaml_text:
            yaml_text = self._rule_path(path).read_text(encoding="utf-8")
        if not yaml_text:
            return {"error": "Pass either yaml_text or path."}
        return validate_rule_text(yaml_text, str(self.rules_dir) if self.rules_dir else None)

    def rule_type_help(self, rule_type):
        cls = RulesLoader.rules_mapping.get(rule_type)
        if cls is None:
            return {"error": f"Unknown rule type '{rule_type}'.", "known_types": sorted(RulesLoader.rules_mapping)}
        return {"rule_type": rule_type, "required_options": sorted(cls.required_options),
                "description": (cls.__doc__ or "").strip()}

    def _rule_path(self, path):
        if not self.rules_dir:
            raise ValueError("No rules folder is configured.")
        target = (self.rules_dir / path).resolve()
        if self.rules_dir not in target.parents:
            raise ValueError("Path is outside the rules folder.")
        if not target.is_file():
            raise ValueError(f"No such rule file: {path}")
        return target

    # --- Elasticsearch tools ---------------------------------------------

    def list_indices(self, pattern="*"):
        rows = self._es().get(f"/_cat/indices/{pattern}", params={"format": "json", "h": "index,docs.count,health", "s": "index"})
        return {"indices": [r for r in rows if not r["index"].startswith(".")][:100]}

    def get_mapping(self, index):
        data = self._es().get(f"/{index}/_mapping")
        merged = {}
        for name, body in data.items():
            merged.update(flatten_mapping(body.get("mappings", {}).get("properties", {})))
        return {"index": index, "matched_indices": len(data), "fields": merged}

    def sample_docs(self, index, query=None, size=3, timestamp_field="@timestamp"):
        size = max(1, min(int(size), 5))
        body = {"size": size, "sort": [{timestamp_field: {"order": "desc", "unmapped_type": "date"}}]}
        if query:
            body["query"] = {"query_string": {"query": query}}
        hits = self._es().search(index, body)["hits"]
        total = hits["total"]["value"] if isinstance(hits["total"], dict) else hits["total"]
        return {"total_matching": total, "docs": [h["_source"] for h in hits["hits"]]}

    def _es(self):
        if self.es is None:
            raise ESError("Elasticsearch is not configured. Set the ES URL in the toolbar.")
        return self.es


def _dump(result):
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f' ... [truncated, {len(text)} chars total]'
    return text


def es_client_from_settings(url, username=None, password=None, ca_certs=None):
    """ca_certs: path to the cluster's CA file (e.g. config/certs/http_ca.crt for a self-signed local install)."""
    if not url:
        return None
    verify = ca_certs or (os.environ.get("ES_VERIFY_CERTS", "1") != "0")
    return ESClient(url, username=username or None, password=password or None, verify_certs=verify)
