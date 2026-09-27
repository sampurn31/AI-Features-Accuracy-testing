"""
Day-2 Global metamorphic test: same aiSearch, criteria C vs C+extra.

Because retrieval (aiSearch) and scoring (matchingCriteria) are separate, holding
aiSearch fixed and only ADDING one weighted criterion re-scores the SAME retrieved
pool. Invariant, per candidate present in both runs:
  - satisfies the extra criterion  -> score must NOT drop
  - does not satisfy it            -> score must NOT rise
This is judge-free and immune to score-formula ambiguity — it only checks direction.

  source set_env.sh
  python run_sourcing_metamorphic.py [--limit N]
Output: output/sourcing_metamorphic_<ts>.xlsx
"""
import argparse
import datetime
import glob
import json
import os
from collections import Counter

import config
import sourcing_client
import sourcing_checks
from run import _resolve_token


def _load_cases():
    p = os.path.join(config.GOLDEN_SOURCING_DIR, "metamorphic_cases.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _process(case, token):
    cid = case["id"]
    query = case["query"]
    base_c = case["base_criteria"]
    extra = case["extra_criterion"]
    row = {"id": cid, "query": query, "extra_criterion": extra.get("text"),
           "expectation": case.get("expectation", "")}
    try:
        sa, ra = sourcing_client.scored_search(ai_search=query, matching_criteria=base_c, page=1, token=token)
        sb, rb = sourcing_client.scored_search(ai_search=query, matching_criteria=base_c + [extra], page=1, token=token)
        ca, cb = ra.get("candidates", []), rb.get("candidates", [])
        overlap = len({c.get("id") for c in ca} & {c.get("id") for c in cb})
        if sa != 200 or sb != 200:
            row.update({"http": f"{sa}/{sb}", "n_a": len(ca), "n_b": len(cb), "overlap": overlap,
                        "violations": 0, "verdict": "error",
                        "detail": (ra.get("error") or rb.get("error") or "")[:400]})
            return row
        viol = sourcing_checks.metamorphic_extra_criterion(ca, cb, extra.get("text"))
        row.update({
            "http": f"{sa}/{sb}", "n_a": len(ca), "n_b": len(cb), "overlap": overlap,
            "violations": len(viol),
            "verdict": ("no_data" if overlap == 0 else "pass" if not viol else "fail"),
            "detail": (" | ".join(viol)[:600] if viol else
                       ("no candidate overlap between runs (pool churn / cache miss)" if overlap == 0
                        else f"{overlap} candidates tracked across both runs, all moved in the correct direction")),
        })
    except Exception as e:  # noqa: BLE001
        row.update({"http": "ERROR", "n_a": 0, "n_b": 0, "overlap": 0, "violations": 0,
                    "verdict": "error", "detail": f"{type(e).__name__}: {e}"})
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    config.require_runtime_env()
    cases = _load_cases()
    if args.limit:
        cases = cases[: args.limit]
    token = _resolve_token()
    print(f"Neptune: {config.RCRM_NEPTUNE_URL}/{config.SOURCING_SEARCH_PATH}")
    print(f"Running {len(cases)} metamorphic cases (2 searches each) ...\n")
    rows = []
    for c in cases:
        r = _process(c, token)
        rows.append(r)
        print(f"  {r['id']:<22} {r['verdict']:<8} overlap={r['overlap']:<3} violations={r['violations']}")
    print("\nverdict:", dict(Counter(r["verdict"] for r in rows)))
    _write_xlsx(rows)


def _write_xlsx(rows, prefix="sourcing_metamorphic"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(config.OUTPUT_DIR, f"{prefix}_{stamp}.xlsx")
    cols = ["id", "verdict", "http", "n_a", "n_b", "overlap", "violations",
            "extra_criterion", "detail", "query", "expectation"]
    colormap = {"pass": "C6EFCE", "fail": "FFC7CE", "no_data": "D9D9D9", "error": "808080"}
    wb = Workbook(); ws = wb.active; ws.title = "metamorphic"
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c, "") for c in cols])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFFFF"); c.fill = PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    vi = cols.index("verdict")
    for rc in ws.iter_rows(min_row=2):
        for c in rc:
            c.alignment = Alignment(vertical="top", wrap_text=True)
        v = str(rc[vi].value)
        if v in colormap:
            rc[vi].fill = PatternFill("solid", fgColor=colormap[v]); rc[vi].font = Font(bold=True)
    for name, w in {"id": 22, "verdict": 9, "http": 8, "n_a": 6, "n_b": 6, "overlap": 8,
                    "violations": 10, "extra_criterion": 34, "detail": 70, "query": 40,
                    "expectation": 44}.items():
        ws.column_dimensions[ws.cell(row=1, column=cols.index(name) + 1).column_letter].width = w
    wb.save(out)
    print(f"Wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
