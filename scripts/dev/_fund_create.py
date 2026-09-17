# ruff: noqa
import json
import sys
import urllib.request

base = "http://127.0.0.1:8000"


def call(method, path, body=None):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, r.read().decode()[:1500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:1500]
    except Exception as e:
        return -1, str(e)[:300]


print("GET funds", call("GET", "/rebalancer"))
if len(sys.argv) > 1 and sys.argv[1] == "create":
    payload = json.load(open("/tmp/_fund_trap.json", encoding="utf-8"))
    print("POST", call("POST", "/rebalancer", payload))
    print("GET funds", call("GET", "/rebalancer"))
