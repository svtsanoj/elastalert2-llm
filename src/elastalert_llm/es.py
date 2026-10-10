"""A tiny read-only Elasticsearch client over plain HTTP (works with ES 8.x and 9.x)."""
import requests


class ESError(Exception):
    pass


class ESClient:
    def __init__(self, url="http://localhost:9200", username=None, password=None, api_key=None, verify_certs=True, timeout=20):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify_certs
        self.timeout = timeout
        if api_key:
            self.session.headers["Authorization"] = f"ApiKey {api_key}"
        elif username:
            self.session.auth = (username, password or "")

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def search(self, index, body):
        return self._request("POST", f"/{index}/_search", json=body)

    def _request(self, method, path, **kwargs):
        try:
            r = self.session.request(method, self.url + path, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise ESError(f"Can't reach Elasticsearch at {self.url}. Is the stack running? ({e})") from e
        if r.status_code >= 400:
            raise ESError(f"Elasticsearch returned HTTP {r.status_code}: {r.text[:400]}")
        return r.json()


def flatten_mapping(properties, prefix=""):
    """{'a': {'properties': {'b': {'type': 'keyword'}}}} -> {'a.b': 'keyword'} (multi-fields included)."""
    fields = {}
    for name, spec in properties.items():
        path = f"{prefix}{name}"
        if "properties" in spec:
            fields.update(flatten_mapping(spec["properties"], path + "."))
        else:
            fields[path] = spec.get("type", "object")
        for sub, sub_spec in (spec.get("fields") or {}).items():
            fields[f"{path}.{sub}"] = sub_spec.get("type", "?")
    return fields
