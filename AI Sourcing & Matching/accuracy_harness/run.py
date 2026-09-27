"""
Orchestrator: for each golden case,
  1. Output A  = current implementation  (RCRM /v2/ai-matching/configuration)
  2. Output B  = reference               (Sonnet 5 running the real Neptune prompt)
  3. Verdict   = judge (Sonnet 5) comparing A vs B against the rubric
Everything is written to a colour-coded .xlsx (a readable "summary" sheet + a
separate "raw_io" sheet holding the big A/B JSON) so results — including the
judge's reasoning — can be validated by hand rather than trusted blindly.

Cases run concurrently (ACCURACY_WORKERS, default 6) so ~100 cases finish in minutes.

Run:
  source set_env.sh            # ANTHROPIC_API_KEY (+ optional overrides)
  python run.py                # runs every golden/*.json case
  python run.py --limit 5      # smoke test a few
"""
import argparse
import datetime
import glob
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import rcrm_client
import rcrm_auth
import llm

WORKERS = int(os.environ.get("ACCURACY_WORKERS", "6"))


def _load_text(path, what):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise SystemExit(f"Missing {what}: {path}\nDrop the real content in before running.")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if "<<<" in text and ">>>" in text:  # placeholder marker still present
        raise SystemExit(f"{what} still contains a placeholder — replace {path} with the real content.")
    return text


def _load_golden():
    """Load every golden/*.json file (each a list of case objects)."""
    cases = []
    for path in sorted(glob.glob(os.path.join(config.GOLDEN_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        for c in loaded:
            c.setdefault("_source", os.path.basename(path))
        cases.extend(loaded)
    return cases


def _stringify_current(status, data):
    # what the judge / reviewer sees for Output A
    return {"http_status": status, **(data if isinstance(data, dict) else {"_raw": data})}


def _resolve_token():
    """Use a pasted token if present, else log in the pre-fed test account."""
    if config.RCRM_ALBATROSS_TOKEN:
        print("Auth: using RCRM_ALBATROSS_TOKEN from env.")
        return config.RCRM_ALBATROSS_TOKEN
    print(f"Auth: logging in {config.LOGIN_EMAIL} on '{config.RCRM_ENV}' ({config.LOGIN_URL}) ...")
    info = rcrm_auth.login()
    print(f"  -> token acquired (account: {info.get('account')}).")
    return info["token"]


def _process(idx, case, rubric, token, prompts):
    """Run one case end to end. Never raises — errors become an 'error' row."""
    cid = case.get("id", f"case-{idx}")
    db = int(config.FORCE_DB) if config.FORCE_DB else int(case.get("database_type", config.DB_GLOBAL_DATABASE))
    text = case["input"]
    base = {
        "id": cid, "kind": case.get("kind", ""), "term": case.get("term", ""),
        "database_type": db, "input": text, "expectation": case.get("expectation", ""),
    }
    try:
        if db == config.DB_GLOBAL_DATABASE:
            status, current = rcrm_client.get_configuration(ai_search=text, database_type=db, token=token)
        else:
            status, current = rcrm_client.get_configuration(
                job_slug=(case.get("job_slug") or config.JOB_ANCHOR), jd_text=text,
                database_type=db, token=token)
        reference = llm.reference_configuration(prompts[db], text)
        verdict = llm.judge(rubric, text, _stringify_current(status, current), reference,
                            expectation=case.get("expectation"))
        base.update({
            "current_http": status,
            "current_output": json.dumps(current, ensure_ascii=False, indent=2),
            "reference_output": reference,
            "verdict": verdict.get("verdict", ""),
            "confidence": verdict.get("confidence", ""),
            "expectation_met": verdict.get("expectation_met", ""),
            "current_issues": verdict.get("current_issues", ""),
            "reference_issues": verdict.get("reference_issues", ""),
            "judge_reasoning": verdict.get("reasoning", ""),
        })
    except Exception as e:  # noqa: BLE001 — keep the batch alive
        base.update({
            "current_http": "ERROR", "current_output": "", "reference_output": "",
            "verdict": "error", "confidence": "", "expectation_met": "",
            "current_issues": f"{type(e).__name__}: {e}", "reference_issues": "", "judge_reasoning": "",
        })
    return idx, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    args = ap.parse_args()

    config.require_runtime_env()
    rubric = _load_text(config.JUDGE_RUBRIC, "judge rubric")

    cases = _load_golden()
    if args.limit:
        cases = cases[: args.limit]

    token = _resolve_token()
    eff_dbs = ({int(config.FORCE_DB)} if config.FORCE_DB
               else {int(c.get("database_type", config.DB_GLOBAL_DATABASE)) for c in cases})
    if config.FORCE_DB and int(config.FORCE_DB) != config.DB_GLOBAL_DATABASE and not config.JOB_ANCHOR:
        config.JOB_ANCHOR = rcrm_auth.get_job_anchor(token)
        print(f"db=1 job anchor: {config.JOB_ANCHOR}")
    prompts = {db: _load_text(config.neptune_prompt_path(db), f"Neptune prompt (db={db})")
               for db in sorted(eff_dbs)}
    print(f"RCRM job-service: {config.RCRM_JOB_SERVICE_URL}  | FORCE_DB={config.FORCE_DB or '(per-case)'}")
    print(f"Running {len(cases)} cases, {WORKERS} workers ...\n")

    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_process, i, c, rubric, token, prompts) for i, c in enumerate(cases, 1)]
        for fut in as_completed(futs):
            i, row = fut.result()
            results[i] = row
            done += 1
            print(f"[{done}/{len(cases)}] {row['id']:<34} {str(row['verdict']):<16} exp={row.get('expectation_met')}")

    rows = [results[i] for i in sorted(results)]
    print("\nverdict tally    :", dict(Counter(str(r['verdict']) for r in rows)))
    print("expectation tally:", dict(Counter(str(r['expectation_met']) for r in rows)))
    print("http tally       :", dict(Counter(str(r['current_http']) for r in rows)))
    if rows:
        _write_xlsx(rows)


# ── Excel output: readable summary sheet (colour-coded) + separate raw_io sheet ──

_HEADER_FILL = "FF305496"        # dark blue
_VERDICT_FILL = {
    "current_better": ("C6EFCE", "006100"),    # green
    "equivalent":     ("D9E1F2", "1F3864"),    # light blue
    "reference_better": ("FFEB9C", "9C6500"),  # amber
    "both_poor":      ("FFC7CE", "9C0006"),    # red
    "error":          ("808080", "FFFFFF"),    # grey
}
_MET_FILL = {"yes": ("C6EFCE", "006100"), "no": ("FFC7CE", "9C0006"),
             "not_applicable": ("E7E6E6", "3F3F3F")}


def _write_xlsx(rows, prefix="accuracy_results"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(config.OUTPUT_DIR, f"{prefix}_{stamp}.xlsx")

    def fill(hexcode):
        return PatternFill("solid", fgColor=hexcode)

    def style_header(ws, ncols):
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFFFF")
            c.fill = fill(_HEADER_FILL)
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=ncols).column_letter}1"

    wb = Workbook()

    # -- Sheet 1: summary (no giant JSON) --
    ws = wb.active
    ws.title = "summary"
    scols = ["id", "kind", "database_type", "current_http", "verdict", "confidence",
             "expectation_met", "current_issues", "reference_issues", "judge_reasoning",
             "input", "expectation", "term"]
    widths = {"id": 30, "kind": 11, "database_type": 9, "current_http": 8, "verdict": 17,
              "confidence": 8, "expectation_met": 10, "current_issues": 48, "reference_issues": 44,
              "judge_reasoning": 75, "input": 44, "expectation": 48, "term": 12}
    ws.append(scols)
    for r in rows:
        ws.append([r.get(c, "") for c in scols])
    style_header(ws, len(scols))
    vi, mi, hi = scols.index("verdict"), scols.index("expectation_met"), scols.index("current_http")
    for row_cells in ws.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
        v = str(row_cells[vi].value)
        if v in _VERDICT_FILL:
            bg, fg = _VERDICT_FILL[v]; row_cells[vi].fill = fill(bg); row_cells[vi].font = Font(bold=True, color=fg)
        m = str(row_cells[mi].value)
        if m in _MET_FILL:
            bg, fg = _MET_FILL[m]; row_cells[mi].fill = fill(bg); row_cells[mi].font = Font(bold=True, color=fg)
        h = str(row_cells[hi].value)
        row_cells[hi].fill = fill("C6EFCE" if h == "200" else "FFC7CE")
    for name, w in widths.items():
        ws.column_dimensions[ws.cell(row=1, column=scols.index(name) + 1).column_letter].width = w

    # -- Sheet 2: raw_io (the big A/B JSON, kept out of the summary) --
    ws2 = wb.create_sheet("raw_io")
    rcols = ["id", "current_http", "current_output", "reference_output"]
    ws2.append(rcols)
    for r in rows:
        ws2.append([r.get(c, "") for c in rcols])
    style_header(ws2, len(rcols))
    for row_cells in ws2.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    for name, w in {"id": 30, "current_http": 8, "current_output": 100, "reference_output": 100}.items():
        ws2.column_dimensions[ws2.cell(row=1, column=rcols.index(name) + 1).column_letter].width = w

    wb.save(out)
    print(f"\nWrote {len(rows)} rows -> {out}  (sheets: summary, raw_io)")


if __name__ == "__main__":
    main()
