"""Reference data and index templates for the mock bank ("DEMOBANK")."""

OUR_BANK = "DEMOBANK"
OUR_IFSC_PREFIX = "DEMO0"

# city: (state, lat, lon, weight)
CITIES = {
    "Mumbai": ("Maharashtra", 19.0760, 72.8777, 14),
    "Delhi": ("Delhi", 28.6139, 77.2090, 13),
    "Bengaluru": ("Karnataka", 12.9716, 77.5946, 12),
    "Hyderabad": ("Telangana", 17.3850, 78.4867, 9),
    "Chennai": ("Tamil Nadu", 13.0827, 80.2707, 8),
    "Pune": ("Maharashtra", 18.5204, 73.8567, 7),
    "Kolkata": ("West Bengal", 22.5726, 88.3639, 7),
    "Ahmedabad": ("Gujarat", 23.0225, 72.5714, 6),
    "Jaipur": ("Rajasthan", 26.9124, 75.7873, 4),
    "Lucknow": ("Uttar Pradesh", 26.8467, 80.9462, 4),
    "Kochi": ("Kerala", 9.9312, 76.2673, 3),
    "Chandigarh": ("Chandigarh", 30.7333, 76.7794, 3),
}

# Counterparty banks for transfers, with rough market-share weights
BANKS = {"SBI": 25, "HDFC": 15, "ICICI": 13, "AXIS": 9, "PNB": 8, "KOTAK": 7, "BOB": 7, "CANARA": 6, "YESBANK": 5, "IDFC": 5}
UPI_PSPS = ["okaxis", "ybl", "paytm", "oksbi", "okhdfcbank", "ibl", "apl"]
CARD_NETWORKS = {"RUPAY": 45, "VISA": 35, "MASTERCARD": 20}
CARD_BINS = {"RUPAY": "608326", "VISA": "421653", "MASTERCARD": "524193"}

# Per channel: share of traffic, processing service, gateway/network, amount (median, sigma, min, max),
# latency (median ms, sigma), baseline failure rate, failure codes (code, desc, weight).
CHANNELS = {
    "UPI": {
        "weight": 55, "service": "upi-switch", "gateway": "NPCI-UPI", "amount": (350, 1.3, 1, 100000),
        "latency": (350, 0.45), "fail_rate": 0.03,
        "failures": [("Z9", "INSUFFICIENT_FUNDS", 4), ("ZM", "INVALID_MPIN", 3), ("U30", "DEBIT_FAILED", 2),
                     ("U69", "COLLECT_EXPIRED", 1), ("U68", "TIMEOUT", 1), ("XH", "BENEFICIARY_ACCOUNT_INVALID", 1)],
    },
    "POS": {
        "weight": 12, "service": "card-switch", "gateway": "CARD-NETWORK", "amount": (900, 1.0, 20, 200000),
        "latency": (250, 0.4), "fail_rate": 0.04,
        "failures": [("51", "INSUFFICIENT_FUNDS", 4), ("55", "INCORRECT_PIN", 3), ("05", "DO_NOT_HONOUR", 2),
                     ("54", "EXPIRED_CARD", 1), ("61", "EXCEEDS_LIMIT", 1)],
    },
    "ECOM": {
        "weight": 12, "service": "ecom-gateway", "gateway": "CARD-NETWORK", "amount": (1200, 1.1, 10, 300000),
        "latency": (600, 0.5), "fail_rate": 0.05,
        "failures": [("51", "INSUFFICIENT_FUNDS", 3), ("N7", "CVV_MISMATCH", 2), ("05", "DO_NOT_HONOUR", 2),
                     ("OTP", "OTP_FAILED", 3), ("61", "EXCEEDS_LIMIT", 1)],
    },
    "ATM": {
        "weight": 8, "service": "atm-switch", "gateway": "NFS", "amount": (2000, 0.0, 100, 20000),
        "latency": (900, 0.4), "fail_rate": 0.03,
        "failures": [("51", "INSUFFICIENT_FUNDS", 3), ("55", "INCORRECT_PIN", 3), ("61", "EXCEEDS_LIMIT", 1),
                     ("68", "RESPONSE_TIMEOUT", 1), ("CD", "CASH_NOT_DISPENSED", 1)],
    },
    "IMPS": {
        "weight": 7, "service": "imps-hub", "gateway": "NPCI-IMPS", "amount": (5000, 1.1, 1, 500000),
        "latency": (650, 0.45), "fail_rate": 0.015,
        "failures": [("08", "BENEFICIARY_BANK_TIMEOUT", 2), ("M3", "ACCOUNT_BLOCKED", 1), ("51", "INSUFFICIENT_FUNDS", 3),
                     ("M0", "VERIFICATION_FAILED", 1)],
    },
    "NEFT": {
        "weight": 5, "service": "neft-hub", "gateway": "RBI-NEFT", "amount": (25000, 1.2, 1, 2500000),
        "latency": (1500, 0.5), "fail_rate": 0.006,
        "failures": [("R01", "INVALID_ACCOUNT", 2), ("R02", "ACCOUNT_CLOSED", 1), ("51", "INSUFFICIENT_FUNDS", 2)],
    },
    "RTGS": {
        "weight": 1, "service": "rtgs-hub", "gateway": "RBI-RTGS", "amount": (500000, 0.8, 200000, 9000000),
        "latency": (900, 0.4), "fail_rate": 0.003,
        "failures": [("R01", "INVALID_ACCOUNT", 2), ("51", "INSUFFICIENT_FUNDS", 1)],
    },
}
ATM_DENOMINATIONS = [500, 1000, 1500, 2000, 2000, 2500, 3000, 4000, 5000, 5000, 10000]

# category: [(mcc, merchant names)]; ECOM-only categories are marked online
MERCHANT_CATEGORIES = {
    "grocery": ("5411", ["FreshMart", "DailyBasket", "Kirana Plus", "GreenLeaf Stores"], False),
    "fuel": ("5541", ["IndiFuel", "HighwayPetro", "CityFuels"], False),
    "restaurants": ("5812", ["Spice Route", "Udupi Cafe", "Tandoor House", "Biryani Point"], False),
    "pharmacy": ("5912", ["MedPlus Care", "HealthFirst Pharmacy"], False),
    "apparel": ("5651", ["StyleBazaar", "Fabindia Outlet", "TrendSet"], False),
    "jewellery": ("5944", ["Swarna Jewellers", "Kanchan Gold"], False),
    "electronics": ("5732", ["DigiWorld", "Gadget Galaxy", "ShopKart Electronics"], True),
    "food_delivery": ("5814", ["Zapto Foods", "QuickBite"], True),
    "travel": ("4722", ["TravelYatra", "RailEasy", "FlyHigh"], True),
    "utilities": ("4900", ["PowerGrid Bills", "JalBoard Payments"], True),
    "telecom": ("4814", ["TeleRecharge", "MobiTop"], True),
    "gaming": ("7995", ["PlayWin Games", "Fantasy11 Arena"], True),
}

# The service catalogue (also loaded into the bank-service-catalog lookup index)
SERVICES = [
    # service, category, owning team, criticality
    ("upi-switch", "payments", "payments-platform", "P1"),
    ("imps-hub", "payments", "payments-platform", "P1"),
    ("neft-hub", "payments", "payments-platform", "P2"),
    ("rtgs-hub", "payments", "payments-platform", "P2"),
    ("card-switch", "cards", "cards-team", "P1"),
    ("ecom-gateway", "cards", "ecommerce-team", "P1"),
    ("atm-switch", "atm", "atm-ops", "P2"),
    ("core-banking", "core", "cbs-team", "P1"),
    ("auth-service", "digital", "digital-team", "P1"),
    ("mobile-api", "digital", "digital-team", "P1"),
    ("netbanking-gw", "digital", "digital-team", "P2"),
    ("fraud-engine", "risk", "risk-team", "P2"),
]

LOGIN_FAILURES = [("BAD_PASSWORD", 6), ("OTP_EXPIRED", 2), ("OTP_INVALID", 2), ("ACCOUNT_LOCKED", 1)]
# Hour-of-day traffic shape in IST (index 0 = 00:00-01:00). Normalised to a mean of 1 in generator.py.
IST_HOURLY = [0.15, 0.08, 0.05, 0.05, 0.07, 0.15, 0.35, 0.6, 0.9, 1.15, 1.3, 1.4,
              1.45, 1.35, 1.25, 1.2, 1.2, 1.25, 1.4, 1.6, 1.65, 1.4, 0.9, 0.45]

# --- index templates -------------------------------------------------------------------------

KW = {"type": "keyword"}


def _obj(**props):
    return {"properties": props}


TEMPLATES = {
    "bank-txn": {
        "index_patterns": ["bank-txn-*"],
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "txn_id": KW, "rrn": KW, "trace_id": KW,
            "channel": KW, "txn_type": KW, "status": KW, "response_code": KW, "response_desc": KW,
            "amount": {"type": "scaled_float", "scaling_factor": 100}, "currency": KW,
            "latency_ms": {"type": "integer"},
            "service": KW, "gateway": KW,
            "customer": _obj(id=KW, segment=KW, home_city=KW),
            "account": _obj(id=KW, type=KW),
            "card": _obj(masked=KW, network=KW, type=KW, bin=KW),
            "upi": _obj(payer_vpa=KW, payee_vpa=KW, psp=KW, mode=KW),
            "counterparty": _obj(bank=KW, account=KW, ifsc=KW, name=KW, is_new={"type": "boolean"}),
            "merchant": _obj(id=KW, name=KW, mcc=KW, category=KW),
            "atm": _obj(id=KW, site=KW),
            "geo": _obj(city=KW, state=KW, country=KW, location={"type": "geo_point"}),
            "device": _obj(id=KW, os=KW),
            "source": _obj(ip={"type": "ip"}),
            "risk": _obj(score={"type": "integer"}, flags=KW),
        }},
    },
    "bank-applogs": {
        # Deliberately no mapping for 'service_name' / 'level': they appear later through field drift.
        "index_patterns": ["bank-applogs-*"],
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "service": _obj(name=KW, version=KW),
            "log": _obj(level=KW, logger=KW),
            "host": _obj(name=KW),
            "message": {"type": "text"},
            "error": _obj(code=KW, type=KW),
            "http": _obj(status={"type": "integer"}, method=KW, path=KW),
            "latency_ms": {"type": "integer"},
            "txn_id": KW, "trace_id": KW,
        }},
    },
    "bank-ecomgw-logs": {
        # Another team's conventions: epoch-millis 'event_time', short names, status as a string.
        "index_patterns": ["bank-ecomgw-logs-*"],
        "mappings": {"properties": {
            "event_time": {"type": "date", "format": "epoch_millis"},
            "svc": KW, "sev": KW, "resp_code": KW, "merchant_id": KW, "pg_ref": KW,
            "amt_paise": {"type": "long"}, "took": {"type": "integer"},
            "msg": {"type": "text"},
        }},
    },
    "bank-auth": {
        "index_patterns": ["bank-auth-*"],
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "event": _obj(action=KW, outcome=KW),
            "failure_reason": KW,
            "user": _obj(id=KW),
            "customer": _obj(id=KW),
            "channel": KW,
            "source": _obj(ip={"type": "ip"}, geo=_obj(country_iso_code=KW, city=KW, location={"type": "geo_point"})),
            "device": _obj(id=KW, os=KW, is_new={"type": "boolean"}),
            "mfa": _obj(method=KW),
            "user_agent": _obj(original=KW),
        }},
    },
    "bank-metrics": {
        "index_patterns": ["bank-metrics-*"],
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "metricset": KW,
            "service": _obj(name=KW),
            "txn_count": {"type": "integer"}, "failed_count": {"type": "integer"},
            "tps": {"type": "float"}, "error_rate": {"type": "float"},
            "latency": _obj(avg_ms={"type": "integer"}, p95_ms={"type": "integer"}),
            "up": {"type": "integer"},
            "atm": _obj(id=KW, city=KW, status=KW, cash_level_pct={"type": "integer"}, cash_inr={"type": "long"}),
        }},
    },
    # bank-legacy-logs-* has no template on purpose: dynamic mapping makes 'message' text + message.keyword.
}

CATALOG_INDEX = "bank-service-catalog"
CATALOG_MAPPING = {"properties": {"service": KW, "category": KW, "team": KW, "criticality": KW}}
INDEX_PREFIXES = ["bank-txn-", "bank-applogs-", "bank-legacy-logs-", "bank-ecomgw-logs-", "bank-auth-", "bank-metrics-"]
