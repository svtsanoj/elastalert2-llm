"""Generate 'now'-relative banking data: baseline traffic plus injected incidents.

Everything is produced in one chronological pass, so the metrics (computed from the
transactions) and ATM cash levels stay consistent with the raw events.
Incidents add events at fixed rates, so they are detectable whatever time of day you seed.
"""
import datetime as dt
import math
import random
from collections import defaultdict
from dataclasses import dataclass

from elastalert_llm.devtools.bank import reference as ref

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_MEAN = sum(ref.IST_HOURLY) / 24
HOURLY = [h / _MEAN for h in ref.IST_HOURLY]
SERVICE_VERSIONS = {s[0]: f"3.{i % 5}.{i * 7 % 10}" for i, s in enumerate(ref.SERVICES)}
TXN_SERVICES = {spec["service"] for spec in ref.CHANNELS.values()}
ATM_CAPACITY = 2_000_000
ISP_PREFIXES = ["49.36", "106.51", "117.99", "157.48", "183.82", "223.181"]  # Indian mobile/broadband ranges


@dataclass
class Incident:
    name: str
    start: dt.datetime
    end: dt.datetime
    about: str
    detect: str

    def active(self, ts):
        return self.start <= ts < self.end


def iso(ts):
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BankGenerator:
    def __init__(self, now=None, days=7, rate=14.0, seed=42, n_customers=2000):
        self.now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
        self.start = (self.now - dt.timedelta(days=days)).replace(second=0)
        self.minutes = int((self.now - self.start).total_seconds() // 60)
        self.rate = rate
        self.rng = random.Random(seed)
        self._seq = 0
        self._scheduled = defaultdict(list)
        self._stats = {}
        self._build_world(n_customers)
        self.incidents = {i.name: i for i in self._incidents()}
        self._schedule_incidents()

    # ------------------------------------------------------------------ world

    def _build_world(self, n):
        rng = self.rng
        n = max(n, 1000)  # incidents use fixed customer ids up to C100888
        self.city_names = list(ref.CITIES)
        self.city_weights = [ref.CITIES[c][3] for c in self.city_names]
        self.customers = []
        for i in range(n):
            segment = rng.choices(["retail", "premium", "corporate"], [85, 12, 3])[0]
            network = self._weighted(ref.CARD_NETWORKS)
            last4 = f"{rng.randrange(10000):04d}"
            psp = rng.choice(ref.UPI_PSPS)
            cid = f"C{100001 + i}"
            self.customers.append({
                "id": cid, "user_id": f"u{100001 + i}", "segment": segment, "home_city": self._city(),
                "account": {"id": f"{'CA' if segment == 'corporate' else 'SB'}{rng.randrange(10**9, 10**10)}",
                            "type": "current" if segment == "corporate" else "savings"},
                "vpa": f"{cid.lower()}.{rng.randrange(100, 999)}@{psp}", "psp": psp,
                "card": {"masked": f"{ref.CARD_BINS[network]}XXXXXX{last4}", "network": network,
                         "type": "credit" if rng.random() < 0.3 else "debit", "bin": ref.CARD_BINS[network]},
                "device": {"id": "d-%08x" % rng.getrandbits(32), "os": rng.choices(["android", "ios", "web"], [75, 20, 5])[0]},
                "ip": f"{rng.choice(ISP_PREFIXES)}.{rng.randrange(256)}.{rng.randrange(1, 255)}",
            })
        self.customer_weights = [{"retail": 1, "premium": 2, "corporate": 3}[c["segment"]] for c in self.customers]

        self.merchants, self.online_merchants = [], []
        for category, (mcc, names, online) in ref.MERCHANT_CATEGORIES.items():
            for name in names:
                cities = ["online"] if online else rng.sample(self.city_names, 6)
                for city in cities:
                    m = {"id": f"M{rng.randrange(10**5, 10**6)}", "name": name if online else f"{name} {city}",
                         "mcc": mcc, "category": category, "city": city}
                    (self.online_merchants if online else self.merchants).append(m)

        self.atms = []
        for i in range(30):
            city = {6: "Pune", 11: "Mumbai"}.get(i, self._city())
            self.atms.append({"id": f"ATM{i + 1:04d}", "city": city, "site": rng.choice(["branch", "offsite", "mall"]),
                              "cash": int(ATM_CAPACITY * rng.uniform(0.5, 1.0))})
        self.atm_by_id = {a["id"]: a for a in self.atms}

    # -------------------------------------------------------------- incidents

    def _incidents(self):
        def at(start_min, end_min):
            return self.now + dt.timedelta(minutes=start_min), self.now + dt.timedelta(minutes=end_min)

        ist_now = self.now.astimezone(IST)
        odd_hour = ist_now.replace(hour=2, minute=40, second=0)
        if odd_hour > ist_now - dt.timedelta(hours=1):
            odd_hour -= dt.timedelta(days=1)
        odd_hour = odd_hour.astimezone(UTC)

        rows = [
            ("payments-outage", *at(-15, -5),
             "payments-db connection pool exhausted: upi-switch, imps-hub and neft-hub fail ~60% (CBS_UNAVAILABLE) together.",
             "One 'payments' category incident (service->category via bank-service-catalog), not 3 service alerts + per-bank alerts."),
            ("upi-bank-down", *at(-40, -25),
             "UPI transfers to PNB fail with U91 BENEFICIARY_BANK_UNAVAILABLE.",
             "frequency on channel:UPI AND status:FAILED, query_key counterparty.bank (examples/rules/bank/upi_failures_by_bank.yaml)."),
            ("brute-force", *at(-30, -25),
             "80 failed logins for user u100123 from 45.146.164.10 (RU), ending in ACCOUNT_LOCKED.",
             "frequency on event.outcome:failure, query_key source.ip (login_bruteforce.yaml)."),
            ("card-hsm-errors", *at(-45, -35),
             "card-switch HSM timeouts: POS declines 91 ISSUER_UNAVAILABLE and ERROR logs. Happens after the field drift.",
             "card_switch_errors.yaml should fire but does not: debug it (field drift)."),
            ("atm-offline", *at(-60, 0),
             "ATM0007 (Pune) loses its NFS link: no transactions and no heartbeats since.",
             "flatline on metricset:atm, query_key atm.id (atm_heartbeat_flatline.yaml)."),
            ("account-takeover", *at(-70, -55),
             "C100555 logs in from Lagos (NG) on a new device, resets the password, then sends IMPS 4,80,000 and UPI 1,00,000.",
             "Correlation across bank-auth-* and bank-txn-*: new-country login followed by high-value transfers."),
            ("ecom-5xx", *at(-90, -80),
             "e-commerce gateway upstream timeouts: ECOM declines 96 and sev=E / resp_code=503 logs.",
             "frequency on bank-ecomgw-logs-* with timestamp_field event_time, timestamp_type unix_ms (ecom_gateway_errors.yaml)."),
            ("card-velocity", *at(-120, -110),
             "Card of C100777 used 14 times in 10 minutes across Delhi, Mumbai, Kolkata and Bengaluru.",
             "cardinality of geo.city per card.masked (card_velocity.yaml)."),
            ("field-drift", *at(-120, 0),
             "card-switch moves to a new logging library: 'service.name'/'log.level' become 'service_name'/'level'.",
             "Rules filtering on service.name: card-switch silently stop matching."),
            ("card-latency", *at(-180, -150),
             "Slow HSM: POS authorisation latency ~6x and card-switch WARN logs.",
             "metric_aggregation on bank-metrics-* latency.p95_ms per service, or on bank-txn-* latency_ms."),
            ("atm-cash-out", *at(-180, 0),
             "ATM0012 (Mumbai) runs out of cash: status cash_out, withdrawals fail CO CASH_OUT.",
             "any/frequency on metricset:atm AND atm.status:cash_out, query_key atm.id."),
            ("credential-stuffing", *at(-240, -230),
             "185.220.101.45 (DE) tries 200 different users; 3 logins succeed.",
             "cardinality of user.id per source.ip on failed logins."),
            ("mule-fan-in", *at(-360, -330),
             "45 different customers send UPI P2P 9,000-19,999 to quickcash.refund@ybl.",
             "cardinality of upi.payer_vpa per upi.payee_vpa (upi_mule_fan_in.yaml)."),
            ("structuring", *at(-26 * 60, -24 * 60),
             "C100321 sends 9 IMPS transfers of 45,000-49,900 to 3 new beneficiaries within 2 hours.",
             "frequency on amounts just under 50,000, query_key customer.id (imps_structuring.yaml)."),
            ("imps-timeouts", *at(-24 * 60 - 30, -24 * 60 - 10),
             "Yesterday: IMPS 08 BENEFICIARY_BANK_TIMEOUT burst for 20 minutes.",
             "imps_timeouts.yaml should have fired but did not: debug it (term on 'failed' vs keyword 'FAILED')."),
            ("high-value-odd-hour", odd_hour, odd_hour + dt.timedelta(minutes=12),
             "Retail customer C100888 logs in on a new device at ~02:40 IST and sends RTGS 2,75,00,000 to a new beneficiary.",
             "any rule: amount >= 1 crore AND customer.segment:retail (high_value_transfer.yaml)."),
        ]
        return [Incident(*r) for r in rows]

    def _schedule_incidents(self):
        inc = self.incidents
        rng = self.rng

        def every(name, seconds, fn):
            i = inc[name]
            ts = i.start
            while ts < i.end:
                self._at(ts + dt.timedelta(seconds=rng.uniform(0, seconds)), fn)
                ts += dt.timedelta(seconds=seconds)

        def failing_txn(channel, code, desc, latency=None, **kw):
            def fn(ts):
                doc = self._txn(ts, channel, **kw)
                self._fail(doc, code, desc)
                if latency:
                    doc["latency_ms"] = int(rng.uniform(*latency))
                return self._emit_txn(doc, ts)
            return fn

        # payments-outage: fixed-rate failures on the three payments services, plus pool errors
        for ch, per_min in (("UPI", 6), ("IMPS", 3), ("NEFT", 2)):
            every("payments-outage", 60 / per_min, failing_txn(ch, "CBS", "CBS_UNAVAILABLE", latency=(5000, 12000)))
        for svc in ("upi-switch", "imps-hub", "neft-hub"):
            every("payments-outage", 15, lambda ts, s=svc: [self._applog(
                ts, s, "ERROR", "Connection pool exhausted: payments-db (active=200/200, waiting=%d)" % rng.randint(40, 400),
                error={"code": "CBS_UNAVAILABLE", "type": "PoolExhaustedException"})])
        every("payments-outage", 20, lambda ts: [self._applog(ts, "mobile-api", "WARN", "Downstream upi-switch slow: 8.2s",
                                                              http={"status": 504, "method": "POST", "path": "/api/v2/upi/pay"})])

        every("upi-bank-down", 15, failing_txn("UPI", "U91", "BENEFICIARY_BANK_UNAVAILABLE", latency=(8000, 15000), bank="PNB"))
        every("upi-bank-down", 60, lambda ts: [self._applog(ts, "upi-switch", "WARN", "NPCI returned U91 for beneficiary bank PNB")])
        every("ecom-5xx", 10, failing_txn("ECOM", "96", "SYSTEM_MALFUNCTION", latency=(10000, 30000)))
        every("card-hsm-errors", 12, failing_txn("POS", "91", "ISSUER_UNAVAILABLE", latency=(4000, 9000)))
        every("card-hsm-errors", 20, lambda ts: [self._applog(ts, "card-switch", "ERROR", "HSM timeout after 4000ms (pool hsm-02)",
                                                              error={"code": "HSM_TIMEOUT", "type": "TimeoutException"})])
        every("card-latency", 20, lambda ts: self._emit_txn(self._txn(ts, "POS", latency=int(rng.uniform(2500, 4500))), ts))
        every("imps-timeouts", 12, failing_txn("IMPS", "08", "BENEFICIARY_BANK_TIMEOUT", latency=(30000, 30500)))
        every("atm-cash-out", 600, failing_txn("ATM", "CO", "CASH_OUT", atm="ATM0012"))

        self._at(inc["atm-offline"].start, lambda ts: [self._legacy(ts, "ERROR", "NFS link down, ATM moved to OFFLINE", "ATM0007", "91")])
        self._at(inc["atm-cash-out"].start, lambda ts: self._drain_atm("ATM0012"))

        self._schedule_brute_force(inc["brute-force"])
        self._schedule_stuffing(inc["credential-stuffing"])
        self._schedule_takeover(inc["account-takeover"])
        self._schedule_velocity(inc["card-velocity"])
        self._schedule_mule(inc["mule-fan-in"])
        self._schedule_structuring(inc["structuring"])
        self._schedule_high_value(inc["high-value-odd-hour"])

    def _spread(self, incident, count):
        span = (incident.end - incident.start).total_seconds()
        return sorted(incident.start + dt.timedelta(seconds=self.rng.uniform(0, span)) for _ in range(count))

    def _schedule_brute_force(self, i):
        cust = self._customer("C100123")
        for n, ts in enumerate(self._spread(i, 80)):
            reason = "BAD_PASSWORD" if n < 10 else "ACCOUNT_LOCKED"
            self._at(ts, lambda t, r=reason: [self._login(t, cust, "failure", r, ip="45.146.164.10", country="RU", city="Moscow",
                                                          device={"id": "d-bf000001", "os": "web"}, is_new=True)])

    def _schedule_stuffing(self, i):
        users = self.rng.sample(self.customers, 120)
        for n, ts in enumerate(self._spread(i, 200)):
            if n < len(users):
                cust, outcome = users[n], ("success" if n in (17, 64, 101) else "failure")
                reason = None if outcome == "success" else "BAD_PASSWORD"
            else:
                cust = {**self.customers[0], "id": None, "user_id": f"user{self.rng.randrange(10**6)}"}
                outcome, reason = "failure", "UNKNOWN_USER"
            self._at(ts, lambda t, c=cust, o=outcome, r=reason: [self._login(
                t, c, o, r, ip="185.220.101.45", country="DE", city="Frankfurt", device={"id": "d-cs000001", "os": "web"},
                is_new=True, user_agent="python-requests/2.31")])

    def _schedule_takeover(self, i):
        cust = self._customer("C100555")
        attacker = dict(ip="102.89.34.12", country="NG", city="Lagos", device={"id": "d-at000555", "os": "android"}, is_new=True)
        s = i.start
        self._at(s, lambda t: [self._login(t, cust, "success", **attacker)])
        self._at(s + dt.timedelta(minutes=2), lambda t: [self._login(t, cust, "success", action="password_reset", **attacker)])
        for minutes, channel, amount in ((8, "IMPS", 480000), (10, "UPI", 100000)):
            def fn(t, ch=channel, amt=amount):
                doc = self._txn(t, ch, customer=cust, amount=amt, new_counterparty=True)
                doc["device"], doc["source"] = dict(attacker["device"]), {"ip": attacker["ip"]}
                doc["risk"] = {"score": 72, "flags": ["NEW_DEVICE", "NEW_BENEFICIARY", "FOREIGN_IP"]}
                return self._emit_txn(doc, t)
            self._at(s + dt.timedelta(minutes=minutes), fn)

    def _schedule_velocity(self, i):
        cust = self._customer("C100777")
        cities = ["Delhi", "Mumbai", "Delhi", "Kolkata", "Mumbai", "Bengaluru", "Delhi", "Kolkata",
                  "Bengaluru", "Mumbai", "Delhi", "Kolkata", "Bengaluru", "Mumbai"]
        for n, ts in enumerate(self._spread(i, len(cities))):
            def fn(t, n=n):
                channel = "ECOM" if n % 3 == 0 else "POS"
                category = ["electronics", "jewellery", "apparel"][n % 3]  # ECOM, POS, POS
                doc = self._txn(t, channel, customer=cust, city=cities[n], amount=round(self.rng.uniform(4000, 45000), 2),
                                merchant_category=category)
                doc["risk"] = {"score": min(40 + n * 5, 98), "flags": ["VELOCITY"] + (["GEO_IMPOSSIBLE"] if n > 2 else [])}
                if n >= 10:
                    self._fail(doc, "59", "SUSPECTED_FRAUD")
                out = self._emit_txn(doc, t)
                if n in (5, 10):
                    out.append(self._applog(t, "fraud-engine", "WARN", f"Velocity rule hit for card {cust['card']['masked']} "
                                            f"(score {doc['risk']['score']})"))
                return out
            self._at(ts, fn)

    def _schedule_mule(self, i):
        payers = self.rng.sample(self.customers, 45)
        for cust, ts in zip(payers, self._spread(i, 45)):
            def fn(t, c=cust):
                doc = self._txn(t, "UPI", customer=c, amount=round(self.rng.uniform(9000, 19999), 2), bank="YESBANK",
                                payee_vpa="quickcash.refund@ybl")
                doc["risk"]["flags"] = ["FAN_IN_PAYEE"]
                return self._emit_txn(doc, t)
            self._at(ts, fn)

    def _schedule_structuring(self, i):
        cust = self._customer("C100321")
        beneficiaries = [(b, f"{self.rng.randrange(10**11, 10**12)}") for b in ("KOTAK", "IDFC", "YESBANK")]
        for n, ts in enumerate(self._spread(i, 9)):
            def fn(t, n=n):
                bank, account = beneficiaries[n % 3]
                doc = self._txn(t, "IMPS", customer=cust, amount=round(self.rng.uniform(45000, 49900), 0), bank=bank,
                                new_counterparty=n < 3)
                doc["counterparty"]["account"] = account
                return self._emit_txn(doc, t)
            self._at(ts, fn)

    def _schedule_high_value(self, i):
        cust = self._customer("C100888")
        device = {"id": "d-hv000888", "os": "web"}
        self._at(i.start, lambda t: [self._login(t, cust, "success", device=device, is_new=True, channel="netbanking")])

        def fn(t):
            doc = self._txn(t, "RTGS", customer=cust, amount=27500000, bank="YESBANK", new_counterparty=True)
            doc["device"] = dict(device)
            doc["risk"] = {"score": 81, "flags": ["HIGH_VALUE", "ODD_HOUR", "NEW_BENEFICIARY", "NEW_DEVICE"]}
            return self._emit_txn(doc, t)
        self._at(i.start + dt.timedelta(minutes=9), fn)

    # ------------------------------------------------------------- generation

    def generate(self):
        """Yield (index, doc) for everything, in time order per minute."""
        rng = self.rng
        bucket_start = self.start
        for m in range(self.minutes):
            ts0 = self.start + dt.timedelta(minutes=m)
            if m % 5 == 0 and m:
                yield from self._flush_metrics(bucket_start)
                bucket_start = ts0
            if m % 10 == 0:
                yield from self._atm_heartbeats(ts0)
            if m % 5 == 0:
                yield from self._service_logs(ts0)

            factor = self._traffic_factor(ts0)
            for _ in range(self._poisson(self.rate * factor)):
                ts = ts0 + dt.timedelta(seconds=rng.uniform(0, 60))
                channel = self._channel(ts)
                doc = self._txn(ts, channel)
                self._baseline_outcome(doc, ts)
                yield from self._emit_txn(doc, ts)
            for _ in range(self._poisson(self.rate * 0.13 * factor)):
                ts = ts0 + dt.timedelta(seconds=rng.uniform(0, 60))
                yield from self._baseline_login(ts)
            for ts, fn in self._scheduled.pop(m, []):
                yield from fn(ts)
        yield from self._flush_metrics(bucket_start)
        for service, category, team, criticality in ref.SERVICES:
            yield ref.CATALOG_INDEX, {"service": service, "category": category, "team": team, "criticality": criticality}

    def _at(self, ts, fn):
        m = int((ts - self.start).total_seconds() // 60)
        if 0 <= m < self.minutes:
            self._scheduled[m].append((ts, fn))

    # ---------------------------------------------------------- transactions

    def _txn(self, ts, channel, customer=None, amount=None, latency=None, city=None, bank=None,
             payee_vpa=None, new_counterparty=False, atm=None, merchant_category=None):
        rng = self.rng
        spec = ref.CHANNELS[channel]
        cust = customer or self._pick_customer()
        self._seq += 1
        city = city or (cust["home_city"] if rng.random() < 0.93 else self._city())
        doc = {
            "@timestamp": iso(ts), "txn_id": f"T{ts:%y%m%d}{self._seq:08d}", "rrn": str(rng.randrange(10**11, 10**12)),
            "trace_id": "%016x" % rng.getrandbits(64), "channel": channel, "txn_type": "DEBIT",
            "status": "SUCCESS", "response_code": "00", "response_desc": "APPROVED",
            "amount": amount if amount is not None else self._amount(channel), "currency": "INR",
            "latency_ms": latency or self._latency(channel), "service": spec["service"], "gateway": spec["gateway"],
            "customer": {"id": cust["id"], "segment": cust["segment"], "home_city": cust["home_city"]},
            "account": dict(cust["account"]),
            "risk": {"score": rng.randint(1, 30), "flags": []},
        }
        mobile = {"device": dict(cust["device"]), "source": {"ip": cust["ip"]}}
        if channel == "UPI":
            if payee_vpa or rng.random() < 0.45:
                mode, payee = "P2P", payee_vpa or self._pick_customer()["vpa"]
            else:
                merchant = self._merchant(city, online=rng.random() < 0.3)
                mode, payee = "P2M", f"{merchant['id'].lower()}@{rng.choice(ref.UPI_PSPS)}"
                doc["merchant"] = self._merchant_doc(merchant)
            doc["upi"] = {"payer_vpa": cust["vpa"], "payee_vpa": payee, "psp": cust["psp"], "mode": mode}
            doc["counterparty"] = {"bank": bank or self._weighted(ref.BANKS), "is_new": new_counterparty}
            doc.update(mobile)
        elif channel in ("POS", "ECOM"):
            online = channel == "ECOM"
            merchant = self._merchant(city, online=online, category=merchant_category)
            doc["card"] = dict(cust["card"])
            doc["merchant"] = self._merchant_doc(merchant)
            if online:
                doc.update(mobile)
        elif channel == "ATM":
            machine = self.atm_by_id[atm] if atm else self._pick_atm(city, ts)
            city = machine["city"]
            doc.update(txn_type="WITHDRAWAL", card=dict(cust["card"]), atm={"id": machine["id"], "site": machine["site"]})
            if not atm and machine["cash"] < doc["amount"]:
                self._fail(doc, "CO", "CASH_OUT")
        else:  # IMPS / NEFT / RTGS
            cp_bank = bank or self._weighted(ref.BANKS)
            doc["txn_type"] = "TRANSFER"
            doc["counterparty"] = {"bank": cp_bank, "account": str(rng.randrange(10**11, 10**12)),
                                   "ifsc": f"{cp_bank[:4]}0{rng.randrange(10**5, 10**6)}", "is_new": new_counterparty}
            doc.update(mobile)
        doc["geo"] = self._geo(city)
        return doc

    def _baseline_outcome(self, doc, ts):
        rng, channel = self.rng, doc["channel"]
        inc = self.incidents
        if doc["status"] != "SUCCESS":
            return
        if inc["payments-outage"].active(ts) and channel in ("UPI", "IMPS", "NEFT") and rng.random() < 0.6:
            self._fail(doc, "CBS", "CBS_UNAVAILABLE")
            doc["latency_ms"] = int(rng.uniform(5000, 12000))
        elif inc["upi-bank-down"].active(ts) and channel == "UPI" and doc["counterparty"]["bank"] == "PNB" and rng.random() < 0.85:
            self._fail(doc, "U91", "BENEFICIARY_BANK_UNAVAILABLE")
        elif inc["ecom-5xx"].active(ts) and channel == "ECOM" and rng.random() < 0.5:
            self._fail(doc, "96", "SYSTEM_MALFUNCTION")
        elif rng.random() < ref.CHANNELS[channel]["fail_rate"]:
            code, desc, _ = rng.choices(ref.CHANNELS[channel]["failures"], [f[2] for f in ref.CHANNELS[channel]["failures"]])[0]
            self._fail(doc, code, desc)
        if inc["card-latency"].active(ts) and channel == "POS":
            doc["latency_ms"] *= 6

    def _fail(self, doc, code, desc):
        doc["status"] = "REVERSED" if code == "CD" else "FAILED"
        doc["response_code"], doc["response_desc"] = code, desc

    def _emit_txn(self, doc, ts):
        """Record stats for metrics and return the txn plus the logs it causes."""
        stats = self._stats.setdefault(doc["service"], [0, 0, []])
        stats[0] += 1
        stats[1] += doc["status"] != "SUCCESS"
        stats[2].append(doc["latency_ms"])
        if doc["channel"] == "ATM" and doc["status"] == "SUCCESS":
            self.atm_by_id[doc["atm"]["id"]]["cash"] -= int(doc["amount"])
        return [(self._index("bank-txn-", ts), doc)] + self._txn_logs(doc, ts)

    # ------------------------------------------------------------------ logs

    def _txn_logs(self, doc, ts):
        rng, failed = self.rng, doc["status"] != "SUCCESS"
        service, channel = doc["service"], doc["channel"]
        if channel == "ECOM":
            if failed or rng.random() < 0.2:
                resp = {"96": "503", "51": "402", "05": "402"}.get(doc["response_code"], "200" if not failed else "400")
                return [(self._index("bank-ecomgw-logs-", ts), {
                    "event_time": int(ts.timestamp() * 1000), "svc": "pg-core", "sev": "E" if failed else "I",
                    "resp_code": resp, "merchant_id": doc["merchant"]["id"], "pg_ref": doc["txn_id"],
                    "amt_paise": int(doc["amount"] * 100), "took": doc["latency_ms"],
                    "msg": (f"payment declined rc={doc['response_code']} {doc['response_desc']}" if failed else "payment authorised"),
                })]
            return []
        if channel == "ATM":
            if failed or rng.random() < 0.1:
                level = "ERROR" if failed else "INFO"
                text = f"withdrawal {'declined ' + doc['response_desc'] if failed else 'ok'} amt={int(doc['amount'])}"
                return [self._legacy(ts, level, text, doc["atm"]["id"], doc["response_code"], doc["txn_id"])]
            return []
        if failed:
            message = f"txn {doc['txn_id']} failed rc={doc['response_code']} {doc['response_desc']} in {doc['latency_ms']}ms"
            return [self._applog(ts, service, "ERROR", message, txn=doc, error={"code": doc["response_desc"], "type": "TxnDeclined"})]
        if service == "card-switch" and doc["latency_ms"] > 2000:
            return [self._applog(ts, service, "WARN", f"Slow authorisation: HSM responded in {doc['latency_ms']}ms", txn=doc)]
        if rng.random() < 0.04:
            return [self._applog(ts, service, "INFO", f"txn {doc['txn_id']} processed in {doc['latency_ms']}ms", txn=doc)]
        return []

    def _applog(self, ts, service, level, message, txn=None, error=None, http=None):
        doc = {"@timestamp": iso(ts), "host": {"name": f"{service}-{self.rng.randint(1, 3):02d}"}, "message": message}
        if service == "card-switch" and self.incidents["field-drift"].active(ts):
            doc.update(service_name=service, level=level, logger="structlog")  # new logging library: flat field names
        else:
            doc.update(service={"name": service, "version": SERVICE_VERSIONS[service]},
                       log={"level": level, "logger": f"com.demobank.{service.replace('-', '.')}"})
        if txn:
            doc.update(txn_id=txn["txn_id"], trace_id=txn["trace_id"], latency_ms=txn["latency_ms"])
        if error:
            doc["error"] = error
        if http:
            doc["http"] = http
        return self._index("bank-applogs-", ts), doc

    def _legacy(self, ts, level, text, atm_id, rc, txn_id=None):
        """Plain-text ATM switch log line; only @timestamp, host and message are separate fields."""
        local = ts.astimezone(IST)
        node = int(atm_id[-2:]) % 3 + 1
        line = f"{local:%Y-%m-%d %H:%M:%S},{local.microsecond // 1000:03d} {level:<5} [ATMSW-0{node}] {text} atm={atm_id} rc={rc}"
        if txn_id:
            line += f" txn={txn_id}"
        return self._index("bank-legacy-logs-", ts), {"@timestamp": iso(ts), "host": f"atmsw-0{node}", "message": line}

    def _service_logs(self, ts):
        """Background logs from services that don't process transactions directly."""
        rng = self.rng
        yield self._applog(ts, "core-banking", "INFO", f"GL posting batch committed: {rng.randint(200, 3000)} entries")
        yield self._applog(ts, "auth-service", "INFO", f"Active sessions: {rng.randint(800, 9000)}")
        yield self._applog(ts, "fraud-engine", "INFO", f"Scored {rng.randint(50, 900)} transactions in the last 5m")
        for service, path in (("mobile-api", "/api/v2/accounts/summary"), ("netbanking-gw", "/nb/transfer/confirm")):
            status = 200 if rng.random() > 0.03 else rng.choice([500, 502, 503])
            yield self._applog(ts, service, "INFO" if status == 200 else "ERROR", f"GET {path} -> {status}",
                               http={"status": status, "method": "GET", "path": path},
                               error=None if status == 200 else {"code": f"HTTP_{status}", "type": "UpstreamError"})

    # ------------------------------------------------------------------ auth

    def _baseline_login(self, ts):
        rng = self.rng
        cust = self._pick_customer()
        channel = "netbanking" if cust["device"]["os"] == "web" or rng.random() < 0.15 else "mobile_app"
        if rng.random() < 0.07:
            reason = rng.choices([r for r, _ in ref.LOGIN_FAILURES], [w for _, w in ref.LOGIN_FAILURES])[0]
            yield self._login(ts, cust, "failure", reason, channel=channel)
            return
        new_device = rng.random() < 0.02
        yield self._login(ts, cust, "success", channel=channel, is_new=new_device)
        if channel == "netbanking":
            yield self._login(ts + dt.timedelta(seconds=rng.uniform(5, 40)), cust, "success", action="otp_verify", channel=channel)

    def _login(self, ts, cust, outcome, reason=None, ip=None, country="IN", city=None, device=None, is_new=False,
               action="login", channel="mobile_app", user_agent=None):
        city = city or cust["home_city"]
        lat, lon = (ref.CITIES[city][1], ref.CITIES[city][2]) if city in ref.CITIES else (0.0, 0.0)
        if city == "Moscow":
            lat, lon = 55.7558, 37.6173
        elif city == "Frankfurt":
            lat, lon = 50.1109, 8.6821
        elif city == "Lagos":
            lat, lon = 6.5244, 3.3792
        device = device or cust["device"]
        doc = {
            "@timestamp": iso(ts), "event": {"action": action, "outcome": outcome},
            "user": {"id": cust["user_id"]}, "channel": channel,
            "source": {"ip": ip or cust["ip"], "geo": {"country_iso_code": country, "city": city, "location": {"lat": lat, "lon": lon}}},
            "device": {"id": device["id"], "os": device["os"], "is_new": is_new},
            "mfa": {"method": "otp_sms" if channel == "netbanking" else "device_binding"},
        }
        if cust.get("id"):
            doc["customer"] = {"id": cust["id"]}
        if reason:
            doc["failure_reason"] = reason
        if user_agent:
            doc["user_agent"] = {"original": user_agent}
        return self._index("bank-auth-", ts), doc

    # --------------------------------------------------------------- metrics

    def _flush_metrics(self, bucket_start):
        total = sum(s[0] for s in self._stats.values())
        for service, *_ in ref.SERVICES:
            if service in TXN_SERVICES:
                count, failed, latencies = self._stats.get(service, [0, 0, []])
            else:
                count = {"core-banking": total, "fraud-engine": total, "auth-service": int(total * 0.15) + 1,
                         "mobile-api": int(total * 0.8) + 1, "netbanking-gw": int(total * 0.15) + 1}[service]
                failed = sum(self.rng.random() < 0.005 for _ in range(min(count, 200)))
                base = {"core-banking": 40, "fraud-engine": 25, "auth-service": 120, "mobile-api": 180, "netbanking-gw": 220}[service]
                latencies = [int(self.rng.lognormvariate(math.log(base), 0.3)) for _ in range(20)]
            latencies = sorted(latencies)
            yield self._index("bank-metrics-", bucket_start), {
                "@timestamp": iso(bucket_start), "metricset": "service", "service": {"name": service},
                "txn_count": count, "failed_count": failed, "tps": round(count / 300, 3),
                "error_rate": round(failed / count, 4) if count else 0.0,
                "latency": {"avg_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
                            "p95_ms": latencies[int(len(latencies) * 0.95)] if latencies else 0},
                "up": 1,
            }
        self._stats = {}

    def _atm_heartbeats(self, ts):
        offline = self.incidents["atm-offline"]
        cash_out = self.incidents["atm-cash-out"]
        for machine in self.atms:
            if machine["id"] == "ATM0007" and offline.active(ts):
                continue
            if machine["cash"] < ATM_CAPACITY * 0.15 and not (machine["id"] == "ATM0012" and cash_out.active(ts)):
                machine["cash"] = ATM_CAPACITY  # refilled by the cash-logistics vendor
            status = "cash_out" if machine["cash"] < 2000 else "online"
            yield self._index("bank-metrics-", ts), {
                "@timestamp": iso(ts), "metricset": "atm",
                "atm": {"id": machine["id"], "city": machine["city"], "status": status,
                        "cash_level_pct": max(0, int(100 * machine["cash"] / ATM_CAPACITY)), "cash_inr": max(0, machine["cash"])},
            }

    def _drain_atm(self, atm_id):
        self.atm_by_id[atm_id]["cash"] = 0
        return []

    # --------------------------------------------------------------- helpers

    def _traffic_factor(self, ts):
        local = ts.astimezone(IST)
        return HOURLY[local.hour] * (0.9 if local.weekday() >= 5 else 1.0)

    def _channel(self, ts):
        weekend = ts.astimezone(IST).weekday() >= 5
        names = list(ref.CHANNELS)
        weights = [ref.CHANNELS[c]["weight"] * (0.4 if weekend and c in ("NEFT", "RTGS") else 1) for c in names]
        return self.rng.choices(names, weights)[0]

    def _amount(self, channel):
        if channel == "ATM":
            return float(self.rng.choice(ref.ATM_DENOMINATIONS))
        median, sigma, lo, hi = ref.CHANNELS[channel]["amount"]
        return round(min(max(self.rng.lognormvariate(math.log(median), sigma), lo), hi), 2)

    def _latency(self, channel):
        median, sigma = ref.CHANNELS[channel]["latency"]
        return int(self.rng.lognormvariate(math.log(median), sigma))

    def _poisson(self, lam):
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, int(round(self.rng.gauss(lam, math.sqrt(lam)))))
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                return k
            k += 1

    def _weighted(self, table):
        return self.rng.choices(list(table), list(table.values()))[0]

    def _city(self):
        return self.rng.choices(self.city_names, self.city_weights)[0]

    def _pick_customer(self):
        return self.rng.choices(self.customers, self.customer_weights)[0]

    def _customer(self, cid):
        return self.customers[int(cid[1:]) - 100001]

    def _merchant(self, city, online=False, category=None):
        pool = self.online_merchants if online else [m for m in self.merchants if m["city"] == city] or self.merchants
        if category:
            source = self.online_merchants if online else self.merchants
            pool = [m for m in source if m["category"] == category] or pool
        return self.rng.choice(pool)

    def _merchant_doc(self, m):
        return {"id": m["id"], "name": m["name"], "mcc": m["mcc"], "category": m["category"]}

    def _pick_atm(self, city, ts):
        offline = self.incidents["atm-offline"].active(ts)
        pool = [a for a in self.atms if a["city"] == city and not (offline and a["id"] == "ATM0007")]
        return self.rng.choice(pool or [a for a in self.atms if a["id"] != "ATM0007"])

    def _geo(self, city):
        state, lat, lon, _ = ref.CITIES[city]
        return {"city": city, "state": state, "country": "IN",
                "location": {"lat": round(lat + self.rng.uniform(-0.08, 0.08), 5), "lon": round(lon + self.rng.uniform(-0.08, 0.08), 5)}}

    def _index(self, prefix, ts):
        return f"{prefix}{ts.astimezone(UTC):%Y.%m.%d}"
