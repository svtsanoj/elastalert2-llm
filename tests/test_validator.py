from elastalert_llm.verify import validate_rule_text

VALID = """
name: errors
type: frequency
index: app-a-logs-*
num_events: 10
timeframe: {minutes: 5}
filter:
- term: {level: ERROR}
realert: {minutes: 30}
alert: [debug]
"""


def test_valid_rule():
    result = validate_rule_text(VALID)
    assert result["ok"], result["errors"]
    assert result["summary"]["type"] == "frequency"


def test_yaml_syntax_error():
    result = validate_rule_text("name: [unclosed")
    assert not result["ok"]
    assert result["errors"][0].startswith("YAML syntax error")


def test_missing_required_option_is_reported_for_the_matching_rule_type():
    result = validate_rule_text("name: x\ntype: flatline\nindex: hb-*\ntimeframe: {minutes: 5}\nalert: debug\n")
    assert result["errors"] == ["Schema error at '(top level)': 'threshold' is a required property"]


def test_wrong_value_type():
    result = validate_rule_text(VALID.replace("timeframe: {minutes: 5}", "timeframe: 5m"))
    assert result["errors"] == ["Schema error at 'timeframe': '5m' is not of type 'object'"]


def test_unknown_rule_type():
    result = validate_rule_text(VALID.replace("type: frequency", "type: frequncy"))
    assert "Unknown rule type 'frequncy'" in result["errors"][0]


def test_alerter_missing_options():
    result = validate_rule_text(VALID.replace("alert: [debug]", "alert: [slack]"))
    assert not result["ok"]
    assert "slack_webhook_url" in result["errors"][0]


def test_lint_hints():
    result = validate_rule_text("name: x\ntype: any\nindex: a\nalert: [email]\nemail: [ops@example.com]\n")
    hints = " ".join(result["warnings"])
    assert "realert" in hints and "filter" in hints and "placeholder" in hints
