"""Direct look at highlightTerms for the shapes that were broken."""
from client import SearchClient
import collections

c = SearchClient(env="ai4")
QUERIES = [
    "((Python OR Java) AND Docker) OR Kubernetes",
    "Python AND NOT (Kubernetes OR Docker)",
    "(Python OR Java) AND Docker",
    "(Python OR Java) AND (Docker OR React)",
    "Python AND NOT Kubernetes",
]
for q in QUERIES:
    meta, cands = c.search(q)
    seen = collections.Counter()
    for x in cands:
        for t in (x.get("highlightTerms") or []):
            seen[str(t)] += 1
    bracket = [t for t in seen if t != t.strip("()")]
    print(f"\nquery: {q}")
    print(f"  highlightTerms: {dict(seen)}")
    print(f"  bracket-carrying terms: {bracket or 'none'}")
