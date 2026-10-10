"""Validate rule YAML with ElastAlert 2's own loader, plus a few deterministic checks.

The loader does the real work: JSON-schema validation, time options, rule type and
alerter lookup, required options, and alerter initialisation. Nothing touches Elasticsearch.
"""
import copy
import datetime
import os
import re

import jsonschema
import yaml
from elastalert.loaders import FileRulesLoader
from elastalert.util import EAException

# A minimal global config, enough for the loader. Rule-level settings override these.
BASE_CONF = {
    "es_host": "localhost",
    "es_port": 9200,
    "writeback_index": "elastalert_status",
    "run_every": datetime.timedelta(minutes=1),
    "buffer_time": datetime.timedelta(minutes=15),
    "rules_folder": ".",
    "alert_time_limit": datetime.timedelta(days=2),
    "old_query_limit": datetime.timedelta(weeks=1),
    "max_query_size": 10000,
    "scroll_keepalive": "30s",
    "max_scrolling_count": 990,
    "disable_rules_on_error": True,
    "scan_subdirectories": True,
    "custom_pretty_ts_format": None,
}

PLACEHOLDER = re.compile(r"<[^<>\n]+>|\bTODO\b|\bCHANGE_?ME\b|\bxxx+\b|example\.(com|org)", re.IGNORECASE)
VIRTUAL_NAME = "__candidate_rule__.yaml"


class _TextRulesLoader(FileRulesLoader):
    """FileRulesLoader that reads the candidate rule from memory; imports still resolve from disk."""

    def __init__(self, conf, rule_path, text):
        super().__init__(conf)
        self._rule_path = rule_path
        self._text = text

    def get_yaml(self, filename):
        if filename == self._rule_path:
            return yaml.safe_load(self._text)
        return super().get_yaml(filename)


def validate_rule_text(text, rules_dir=None):
    """Validate one rule given as YAML text.

    Returns {"ok": bool, "errors": [str], "warnings": [str], "summary": {...}}.
    """
    errors, warnings = [], []
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return _result([f"YAML syntax error: {e}"], [], {})
    if not isinstance(raw, dict):
        return _result(["The rule must be a YAML mapping (key: value pairs)."], [], {})

    summary = {k: raw.get(k) for k in ("name", "type", "index", "alert") if k in raw}
    rule_type = raw.get("type")
    if isinstance(rule_type, str) and "." not in rule_type and rule_type not in FileRulesLoader.rules_mapping:
        known = ", ".join(sorted(FileRulesLoader.rules_mapping))
        return _result([f"Unknown rule type '{rule_type}'. Built-in types: {known}. Custom types use a dotted path."],
                       _lint(raw, text), summary)
    conf = copy.deepcopy(BASE_CONF)
    conf["rules_folder"] = rules_dir or "."
    rule_path = os.path.join(rules_dir or ".", VIRTUAL_NAME)
    loader = _TextRulesLoader(conf, rule_path, text)

    try:
        rule = loader.load_yaml(rule_path)
        schema_errors = _schema_errors(loader, rule)
        if schema_errors:
            return _result(schema_errors, _lint(raw, text), summary)
        loader.load_options(rule, conf, rule_path)
        loader.load_modules(rule)
    except EAException as e:
        errors.append(_clean(str(e)))
    except Exception as e:  # alerter/rule constructors can raise anything
        errors.append(f"{type(e).__name__}: {_clean(str(e))}")

    warnings.extend(_lint(raw, text))
    return _result(errors, warnings, summary)


def _schema_errors(loader, rule):
    """JSON-schema errors as short lines.

    The schema is a top-level oneOf with one branch per rule type, so a plain error just says
    "not valid under any of the given schemas". Report the errors of the branch whose 'type' matched.
    """
    lines = []
    for error in loader.rule_schema.iter_errors(rule):
        if error.validator == "oneOf" and not error.absolute_path:
            lines.extend(_format(e) for e in _matching_branch_errors(error))
        else:
            lines.append(_format(error))
    return lines[:5]


def _matching_branch_errors(error):
    branches = {}
    for sub in error.context:
        branches.setdefault(sub.relative_schema_path[0], []).append(sub)
    matching = [subs for subs in branches.values() if not any(list(s.absolute_path) == ["type"] for s in subs)]
    if len(matching) == 1:
        return matching[0]
    return [jsonschema.exceptions.best_match([error])]


def _format(error):
    path = ".".join(str(p) for p in error.absolute_path)
    return f"Schema error at '{path or '(top level)'}': {_clean(error.message)}"


def _lint(rule, text):
    """Deterministic checks the schema doesn't do. These are hints, not failures."""
    hints = []
    alerts = rule.get("alert")
    alert_names = [a if isinstance(a, str) else next(iter(a), "") for a in (alerts if isinstance(alerts, list) else [alerts])]
    if alert_names and all(a == "debug" for a in alert_names):
        hints.append("The only alerter is 'debug': alerts are written to the ElastAlert log and nobody is notified.")
    if "realert" not in rule:
        hints.append("No 'realert' set: the default is 1 minute, so a long-running problem alerts every minute.")
    if "filter" not in rule:
        hints.append("No 'filter': every document in the index counts.")
    for line in text.splitlines():
        if not line.lstrip().startswith("#") and PLACEHOLDER.search(line):
            hints.append(f"Looks like a placeholder value: '{line.strip()}'")
    return hints


def _clean(message):
    message = message.replace(VIRTUAL_NAME, "rule")
    return message if len(message) < 800 else message[:800] + " ..."


def _result(errors, warnings, summary):
    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": summary}
