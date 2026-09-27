"""Which envs run the old parser vs the new query_string one, and what each says about NOT(A AND B)."""
import json, urllib.request, urllib.error, ssl

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CTX = ssl.create_default_context()

def post(url, body, token=None, timeout=120):
    h = {"content-type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            buf = b""
            while True:
                try: ch = r.read(65536)
                except Exception: break
                if not ch: break
                buf += ch
            return buf.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return f"__HTTP_{e.code}__"
    except Exception as e:
        return f"__ERR_{type(e).__name__}__"

def kw(q): return {"keywordSearch": {"keywords": {"booleanQuery": q}}}

for env in ("ai4", "ai5", "dev"):
    alb = f"https://albatross-{env}.recruitcrm.net/v1"
    nep = f"https://{env}-neptune.recruitcrm.net/v1"
    raw = post(alb + "/login", {"email": os.environ["RCRM_LOGIN_EMAIL"], "password": os.environ["RCRM_LOGIN_PASSWORD"]}, timeout=60)
    if raw.startswith("__"):
        print(f"{env}: login {raw}"); continue
    try: token = json.loads(raw)["data"]["token"]
    except Exception: print(f"{env}: no token"); continue

    def count(q):
        r = post(nep + "/ai-sourcing-v3/search/count", kw(q), token)
        if r.startswith("__"): return r
        try: return json.loads(r)["count"]
        except Exception: return "parse-fail"

    # Which parser? the metadata's boolean_logic shape gives it away.
    r = post(nep + "/ai-sourcing-v3/search", kw("Python AND NOT (Kubernetes AND Docker)"), token)
    shape = "?"
    if not r.startswith("__"):
        for line in r.split("\n"):
            if not line.strip(): continue
            try: d = json.loads(line).get("data")
            except Exception: continue
            if isinstance(d, dict) and d.get("type") == "metadata":
                qu = json.loads(d["queryUsed"])
                bl = (qu.get("boolean_logic") or {}).get("keywords")
                shape = "NEW query_string" if isinstance(bl, dict) and "query" in bl else f"OLD parser {bl}"
                break
    print(f"\n=== {env}  [{shape}] ===")
    for label, q in [("|P|", "Python"),
                     ("|P AND K|", "Python AND Kubernetes"),
                     ("|P AND D|", "Python AND Docker"),
                     ("|P AND K AND D|", "Python AND Kubernetes AND Docker"),
                     ("NOT (K AND D)", "Python AND NOT (Kubernetes AND Docker)"),
                     ("(NOT K OR NOT D)", "Python AND (NOT Kubernetes OR NOT Docker)"),
                     ("NOT (K OR D)", "Python AND NOT (Kubernetes OR Docker)")]:
        print(f"   {label:20} {count(q)}")
