import datetime as dt
from collections import Counter

import pytest

from elastalert_llm.devtools.bank.generator import BankGenerator

NOW = dt.datetime(2026, 10, 11, 9, 30, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def docs():
    gen = BankGenerator(now=NOW, days=1.5, rate=6, seed=7, n_customers=300)
    return gen, list(gen.generate())


def minutes_ago(doc, field="@timestamp"):
    ts = dt.datetime.fromisoformat(doc[field].replace("Z", "+00:00"))
    return (NOW - ts).total_seconds() / 60


def test_all_index_families_present(docs):
    _, rows = docs
    families = Counter(index.rsplit("-", 1)[0] if index[-2:].isdigit() else index for index, _ in rows)
    assert set(families) == {"bank-txn", "bank-applogs", "bank-legacy-logs", "bank-ecomgw-logs", "bank-auth", "bank-metrics",
                             "bank-service-catalog"}


def test_upi_bank_down_failures(docs):
    _, rows = docs
    pnb = [d for i, d in rows if i.startswith("bank-txn-") and d["channel"] == "UPI" and d.get("counterparty", {}).get("bank") == "PNB"
           and d["response_code"] == "U91"]
    assert len(pnb) >= 50 and all(25 <= minutes_ago(d) <= 40 for d in pnb)


def test_atm0007_heartbeats_stop(docs):
    _, rows = docs
    beats = [d for i, d in rows if i.startswith("bank-metrics-") and d["metricset"] == "atm" and d["atm"]["id"] == "ATM0007"]
    assert beats and min(minutes_ago(d) for d in beats) > 60


def test_field_drift_on_card_switch_logs(docs):
    _, rows = docs
    card_logs = [d for i, d in rows if i.startswith("bank-applogs-")
                 and "card-switch" in (d.get("service_name"), d.get("service", {}).get("name"))]
    drifted = [d for d in card_logs if "service_name" in d]
    assert drifted and all(minutes_ago(d) <= 120 for d in drifted)
    assert all("service" not in d and "level" in d for d in drifted)


def test_security_incidents(docs):
    _, rows = docs
    auth = [d for i, d in rows if i.startswith("bank-auth-")]
    assert sum(d["source"]["ip"] == "45.146.164.10" for d in auth) == 80
    stuffing = {d["user"]["id"] for d in auth if d["source"]["ip"] == "185.220.101.45"}
    assert len(stuffing) > 150


def test_card_velocity_cities(docs):
    gen, rows = docs
    card = gen._customer("C100777")["card"]["masked"]
    cities = {d["geo"]["city"] for i, d in rows if i.startswith("bank-txn-") and d.get("card", {}).get("masked") == card
              and 110 <= minutes_ago(d) <= 120}
    assert len(cities) >= 4


def test_deterministic():
    a = [d for _, d in BankGenerator(now=NOW, days=0.05, rate=6, seed=1, n_customers=50).generate()]
    b = [d for _, d in BankGenerator(now=NOW, days=0.05, rate=6, seed=1, n_customers=50).generate()]
    assert a == b
