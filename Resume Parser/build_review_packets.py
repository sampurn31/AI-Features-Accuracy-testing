#!/usr/bin/env python3
"""
Builds one review packet per resume for the ground-truth pass of the
Dev vs Staging comparison: resume text + both parser outputs side by side
+ automatic grounding checks (is the extracted value actually in the resume?).

Input : English_100/, dev_vs_staging_state.json, raw_responses_{staging,dev}_en/
Output: review_packets/<resume>.md
"""
import json, re, subprocess, datetime
from pathlib import Path

SCRIPT_DIR  = Path(__file__).resolve().parent
RESUMES     = SCRIPT_DIR / "English_100"
STATE_FILE  = SCRIPT_DIR / "dev_vs_staging_state.json"
RAW_DIR     = {"Staging": SCRIPT_DIR / "raw_responses_staging_en",
               "Dev":     SCRIPT_DIR / "raw_responses_dev_en"}
OUT_DIR     = SCRIPT_DIR / "review_packets"
MAX_TEXT    = 18000

SCALAR_FIELDS = ["fullName", "email", "phone", "city", "state", "country", "postalCode",
                 "linkedin", "currentTitle", "currentOrg", "workExpYears", "qualification",
                 "specialization", "summary"]


def resume_text(p: Path) -> str:
    if p.suffix.lower() == ".pdf":
        cmd = ["pdftotext", "-layout", str(p), "-"]
    else:
        cmd = ["textutil", "-convert", "txt", "-stdout", str(p)]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9@.+]", "", s.lower())


def _ym(ts):
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m") if ts else ""
    except Exception:
        return ""


def grounding(fields: dict, text: str) -> dict:
    flat, digits = _flat(text), re.sub(r"\D", "", text)
    g = {}
    if fields.get("email"):
        g["email_in_text"] = _flat(fields["email"]) in flat
    ph = re.sub(r"\D", "", fields.get("phone", ""))
    if ph:
        g["phone_in_text"] = ph[-9:] in digits
    skills = [s.strip() for s in re.split(r"[,;|]", fields.get("skills", "")) if s.strip()]
    if skills:
        found = [s for s in skills if _flat(s) and _flat(s) in flat]
        g["skills_total"] = len(skills)
        g["skills_found_verbatim_in_text"] = len(found)
        g["skills_not_in_text"] = [s for s in skills if s not in found]
    return g


def entries(raw: dict, kind: str) -> list:
    out = []
    for e in raw.get(kind, []):
        if kind == "work":
            out.append(f"- {e.get('title','')} | {e.get('work_company_name','')} | "
                       f"{e.get('work_location','')} | {_ym(e.get('work_start_date'))} → "
                       f"{'Present' if e.get('is_currently_working') else _ym(e.get('work_end_date'))}")
        else:
            out.append(f"- {e.get('educational_qualification','')} | {e.get('educational_specialization','')} | "
                       f"{e.get('institute_name','')} | {_ym(e.get('education_start_date'))} → "
                       f"{_ym(e.get('education_end_date'))}")
    return out or ["- (none)"]


def main():
    OUT_DIR.mkdir(exist_ok=True)
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    built = 0
    for name, e in sorted(state.items()):
        if e.get("Staging", {}).get("error") is not None or e.get("Dev", {}).get("error") is not None:
            continue
        text = resume_text(RESUMES / name)
        raw = {l: json.loads((RAW_DIR[l] / f"{name}.json").read_text(encoding="utf-8")) for l in RAW_DIR}
        sf, df = e["Staging"]["fields"], e["Dev"]["fields"]

        md = [f"# {name}", "", "## Parsed fields", "", "| Field | Staging | Dev |", "|---|---|---|"]
        for f in SCALAR_FIELDS:
            md.append(f"| {f} | {sf.get(f,'').replace('|','/')} | {df.get(f,'').replace('|','/')} |")
        md += ["", "## Skills", f"- Staging: {sf.get('skills','')}", f"- Dev: {df.get('skills','')}", ""]
        for label in ("Staging", "Dev"):
            md += [f"## Work history — {label} ({len(raw[label]['work'])} entries)"] + entries(raw[label], "work") + [""]
        for label in ("Staging", "Dev"):
            md += [f"## Education — {label} ({len(raw[label]['education'])} entries)"] + entries(raw[label], "education") + [""]
        md += ["## Automatic grounding checks (value found verbatim in resume text?)",
               f"- Staging: {json.dumps(grounding(sf, text), ensure_ascii=False)}",
               f"- Dev: {json.dumps(grounding(df, text), ensure_ascii=False)}", "",
               f"## Resume text (pdftotext/textutil{', truncated' if len(text) > MAX_TEXT else ''})",
               "```", text[:MAX_TEXT], "```"]
        (OUT_DIR / f"{name}.md").write_text("\n".join(md), encoding="utf-8")
        built += 1
    print(f"built {built} packets → {OUT_DIR}")


if __name__ == "__main__":
    main()
