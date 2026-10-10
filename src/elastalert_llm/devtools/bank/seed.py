"""Load the mock bank data into Elasticsearch.

    elastalert2-llm-bank-seed --list               show the injected incidents
    elastalert2-llm-bank-seed --dry-run            generate and count, send nothing
    elastalert2-llm-bank-seed --reset              delete existing bank-* indices, then load

Connection settings come from flags, environment variables, or a .env file in the current folder:
    ES_URL (default https://localhost:9200), ES_USER (default elastic), ES_PASSWORD, ES_CA_CERTS
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

from elastalert_llm.devtools.bank import reference as ref
from elastalert_llm.devtools.bank.generator import IST, BankGenerator

BATCH = 5000


def load_dotenv(path=".env"):
    """Set KEY=VALUE pairs from a .env file, without overriding real environment variables."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Seeder:
    def __init__(self, url, user, password, ca_certs=None):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.verify = ca_certs or True
        self.session.headers["Content-Type"] = "application/json"

    def request(self, method, path, ok=(200, 201), **kwargs):
        r = self.session.request(method, self.url + path, timeout=120, **kwargs)
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}

    def check(self):
        info = self.request("GET", "/")
        return info["version"]["number"]

    def reset(self):
        rows = self.request("GET", "/_cat/indices/bank-*", params={"format": "json", "h": "index"})
        names = sorted(r["index"] for r in rows
                       if r["index"] == ref.CATALOG_INDEX or any(r["index"].startswith(p) for p in ref.INDEX_PREFIXES))
        for i in range(0, len(names), 20):
            self.request("DELETE", "/" + ",".join(names[i:i + 20]))
        return names

    def setup(self):
        settings = {"number_of_shards": 1, "number_of_replicas": 0}
        meta = {"created_by": "elastalert2-llm bank seed"}
        for name, spec in ref.TEMPLATES.items():
            body = {"index_patterns": spec["index_patterns"], "priority": 500, "_meta": meta,
                    "template": {"settings": settings, "mappings": spec["mappings"]}}
            self.request("PUT", f"/_index_template/{name}", json=body)
        # Settings only: leave bank-legacy-logs-* to dynamic mapping on purpose (text vs keyword trap).
        self.request("PUT", "/_index_template/bank-legacy-logs", json={
            "index_patterns": ["bank-legacy-logs-*"], "priority": 500, "_meta": meta, "template": {"settings": settings}})
        exists = self.session.head(self.url + "/" + ref.CATALOG_INDEX, timeout=30).status_code == 200
        if not exists:
            body = {"settings": {"index": {"mode": "lookup", "number_of_replicas": 0}}, "mappings": ref.CATALOG_MAPPING}
            try:
                self.request("PUT", "/" + ref.CATALOG_INDEX, json=body)
            except RuntimeError:  # lookup mode needs ES 8.18+ / 9.x
                self.request("PUT", "/" + ref.CATALOG_INDEX, json={"settings": settings, "mappings": ref.CATALOG_MAPPING})

    def bulk(self, docs):
        lines = []
        for index, doc in docs:
            lines.append(json.dumps({"index": {"_index": index}}))
            lines.append(json.dumps(doc, separators=(",", ":")))
        body = "\n".join(lines) + "\n"
        result = self.request("POST", "/_bulk", data=body.encode("utf-8"), headers={"Content-Type": "application/x-ndjson"})
        if result.get("errors"):
            failed = [item["index"] for item in result["items"] if item["index"].get("error")]
            raise RuntimeError(f"{len(failed)} documents failed. First error: {json.dumps(failed[0]['error'])[:600]}")

    def refresh(self):
        self.request("POST", "/bank-*/_refresh")


def family(index):
    for prefix in ref.INDEX_PREFIXES:
        if index.startswith(prefix):
            return prefix + "*"
    return index


def print_incidents(gen):
    print(f"Injected incidents (times in IST, now = {gen.now.astimezone(IST):%Y-%m-%d %H:%M}):\n")
    for i in sorted(gen.incidents.values(), key=lambda i: i.start):
        print(f"  {i.name:<20} {i.start.astimezone(IST):%a %H:%M} - {i.end.astimezone(IST):%H:%M}  {i.about}")
        print(f"  {'':<20} detect: {i.detect}\n")


def main(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(description="Seed synthetic Indian banking data into Elasticsearch.")
    p.add_argument("--es", default=os.environ.get("ES_URL", "https://localhost:9200"))
    p.add_argument("--user", default=os.environ.get("ES_USER", "elastic"))
    p.add_argument("--password", default=os.environ.get("ES_PASSWORD", ""))
    p.add_argument("--ca-certs", default=os.environ.get("ES_CA_CERTS"), help="CA file, e.g. <es>/config/certs/http_ca.crt")
    p.add_argument("--days", type=float, default=7, help="days of history (default 7)")
    p.add_argument("--rate", type=float, default=14, help="average transactions per minute (default 14)")
    p.add_argument("--seed", type=int, default=42, help="random seed, for repeatable data")
    p.add_argument("--reset", action="store_true", help="delete existing bank-* indices created by this tool first")
    p.add_argument("--list", action="store_true", help="list the injected incidents and exit")
    p.add_argument("--dry-run", action="store_true", help="generate and count documents, send nothing")
    a = p.parse_args(argv)

    gen = BankGenerator(days=a.days, rate=a.rate, seed=a.seed)
    if a.list:
        print_incidents(gen)
        return 0

    seeder = None
    if not a.dry_run:
        if not a.password:
            p.error("no password: pass --password, set ES_PASSWORD, or put ES_PASSWORD=... in a .env file")
        seeder = Seeder(a.es, a.user, a.password, a.ca_certs)
        try:
            print(f"Connected to Elasticsearch {seeder.check()} at {a.es}")
        except (requests.RequestException, RuntimeError) as e:
            sys.exit(f"Can't connect to {a.es}: {e}")
        if a.reset:
            deleted = seeder.reset()
            print(f"Deleted {len(deleted)} existing bank indices")
        seeder.setup()

    started = time.time()
    counts, samples, batch = Counter(), {}, []
    for index, doc in gen.generate():
        fam = family(index)
        counts[fam] += 1
        samples.setdefault(fam, doc)
        if seeder:
            batch.append((index, doc))
            if len(batch) >= BATCH:
                seeder.bulk(batch)
                batch = []
                print(f"\r  {sum(counts.values()):,} documents sent", end="", flush=True)
    if seeder:
        if batch:
            seeder.bulk(batch)
        seeder.refresh()
        print(f"\r  {sum(counts.values()):,} documents sent")

    print(f"\n{'Dry run: ' if a.dry_run else ''}{sum(counts.values()):,} documents in {time.time() - started:.0f}s")
    for fam, n in sorted(counts.items()):
        print(f"  {fam:<24} {n:>9,}")
    if a.dry_run:
        for fam, doc in sorted(samples.items()):
            print(f"\nSample {fam}:\n{json.dumps(doc, indent=1)[:1500]}")
    print()
    print_incidents(gen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
