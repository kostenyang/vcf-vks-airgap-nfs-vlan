"""Tiny client for the VCF Installer (120.2) / SDDC Manager (120.4) public API."""
import os
import json, ssl, urllib.request
CTX = ssl._create_unverified_context()
class Api:
    def __init__(self, base="https://192.168.120.2", user="admin@local", pw=os.environ["LAB_PASSWORD"]):
        self.base = base; self.user = user; self.pw = pw; self.login()
    def login(self):
        self.tok = self.req("/v1/tokens", "POST", {"username": self.user, "password": self.pw}, auth=False)[1]["accessToken"]
    def req(self, path, method="GET", body=None, auth=True, timeout=120):
        h = {"Content-Type": "application/json"}
        if auth: h["Authorization"] = "Bearer " + self.tok
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, context=CTX, timeout=timeout) as x:
                t = x.read().decode(); return x.status, (json.loads(t) if t else {})
        except urllib.error.HTTPError as e:
            if e.code == 401 and auth:
                self.login(); return self.req(path, method, body, auth, timeout)
            return e.code, e.read().decode()[:2000]
def flatten_errors(e, out=None):
    out = [] if out is None else out
    if isinstance(e, dict):
        m = e.get("message") or e.get("errorCode")
        if m: out.append(m)
        for n in e.get("nestedErrors") or []: flatten_errors(n, out)
    return out
