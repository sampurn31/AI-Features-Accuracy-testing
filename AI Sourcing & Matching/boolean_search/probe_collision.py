"""Do terms that CONTAIN and/or/not get split by the parser?"""
from client import SearchClient
c = SearchClient(env="ai4")
QUERIES = [
    "Android",
    "stand",
    "Oracle",
    "Notion",
    "wahi",
    "Retail OR stand OR pop up",
    "Android AND Oracle",
    "pop up",
]
for q in QUERIES:
    try:
        meta, cands = c.search(q)
        hl = sorted({str(t) for x in cands for t in (x.get("highlightTerms") or [])})
        qu = meta.get("queryUsed")
        import json as j
        kws = j.loads(qu).get("keywords") if qu else None
        print(f"{q!r:32} total={meta.get('totalResults'):<6} highlight={hl}  queryUsed.keywords={kws}")
    except Exception as e:
        print(f"{q!r:32} ERROR {e}")
