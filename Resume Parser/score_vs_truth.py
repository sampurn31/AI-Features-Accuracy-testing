#!/usr/bin/env python3
"""
Scores Staging and Dev parser output against the blind ground-truth answer key
(truth/*.json, built from the resume text only — never from parser output).

Every field gets a status per env: Correct (1) / Partial (0.5) / Wrong (0) /
Missing (0) / Hallucinated (0) / Unverified extra (0.5). Work history and
education are matched entry by entry (company/title or degree/institute),
then dates are checked.

Resume score = weighted mean of field scores (0–100). Winner = higher score if
the gap is >= WIN_MARGIN points, else Tie.

Output: truth_scores.json (consumed by add_truth_verdict_to_report.py)
"""
import json, re, glob, datetime, unicodedata
from difflib import SequenceMatcher
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "dev_vs_staging_state.json"
TRUTH_DIR  = SCRIPT_DIR / "truth"
TEXT_DIR   = SCRIPT_DIR / "truth_packets"
RAW_DIR    = {"Staging": SCRIPT_DIR / "raw_responses_staging_en",
              "Dev":     SCRIPT_DIR / "raw_responses_dev_en"}
OUT        = SCRIPT_DIR / "truth_scores.json"
VERIFY_DIR = SCRIPT_DIR / "verify"     # reviewer overrides of scorer calls (checked against the resume)
ENVS       = ("Staging", "Dev")
WIN_MARGIN = 1.0

WEIGHTS = {
    "fullName": 3, "email": 3, "phone": 3, "city": 2, "state": 1, "country": 1,
    "postalCode": 0.5, "linkedin": 0.5, "currentTitle": 2, "currentOrg": 2,
    "workExpYears": 1.5, "qualification": 1, "skills": 2, "workHistory": 4, "education": 2,
}
FIELDS = list(WEIGHTS)
CRITICAL = ["fullName", "email", "phone", "city"]

STATUS_SCORE = {"Correct": 1.0, "Partial": 0.5, "Unverified extra": 0.5,
                "Wrong": 0.0, "Missing": 0.0, "Hallucinated": 0.0}

# ─────────────────────────────── normalisation ─────────────────────────────

def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def sim(a, b) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    inter = len(ta & tb)
    cont = inter / min(len(ta), len(tb))
    jac = inter / len(ta | tb)
    return max(seq, (cont + jac) / 2)


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def phones_same(a, b) -> bool:
    """Same number ignoring country code / formatting: shorter digit string is a suffix (>= 7 digits)."""
    da, db = digits(a), digits(b)
    n = min(len(da), len(db), 9)
    return n >= 7 and da[-n:] == db[-n:]


EMPTY_WORDS = {"not available", "n a", "na", "none", "nil", "not applicable", "unknown"}


def clean(v) -> str:
    v = str(v or "").strip()
    return "" if norm(v) in EMPTY_WORDS else v


COUNTRY_ALIASES = {
    "it": "italy", "italia": "italy", "uk": "united kingdom", "gb": "united kingdom",
    "great britain": "united kingdom", "england": "united kingdom", "scotland": "united kingdom",
    "us": "united states", "usa": "united states", "united states of america": "united states",
    "uae": "united arab emirates", "deutschland": "germany", "de": "germany",
    "danmark": "denmark", "dk": "denmark", "norge": "norway", "no": "norway",
    "espana": "spain", "es": "spain", "nederland": "netherlands", "the netherlands": "netherlands",
    "holland": "netherlands", "nl": "netherlands", "polska": "poland", "pl": "poland",
    "sverige": "sweden", "se": "sweden", "hrvatska": "croatia", "hr": "croatia",
    "turkiye": "turkey", "in": "india", "bharat": "india", "suomi": "finland", "fi": "finland",
    "danemark": "denmark", "grece": "greece", "hellas": "greece", "gr": "greece",
    "latvija": "latvia", "lv": "latvia", "lietuva": "lithuania", "eesti": "estonia",
    "rossiya": "russia", "russian federation": "russia", "ph": "philippines", "sg": "singapore",
    "lk": "sri lanka", "pk": "pakistan", "kr": "south korea", "korea": "south korea",
    "republic of korea": "south korea", "pt": "portugal", "fr": "france", "ro": "romania",
    "ua": "ukraine", "bg": "bulgaria", "cy": "cyprus", "mt": "malta", "ie": "ireland",
}
US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
    "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}


def canon_place(s, kind) -> str:
    n = norm(s)
    if kind == "country":
        return COUNTRY_ALIASES.get(n, n)
    if kind == "state":
        return US_STATES.get(n, n)
    return n


def canon_linkedin(s) -> str:
    n = str(s or "").lower().strip()
    n = re.sub(r"^https?://", "", n)
    n = re.sub(r"^([a-z]{2,3}\.)?(www\.)?", "", n).rstrip("/")
    m = re.search(r"linkedin\.com/(in|pub)/([^/?#]+)", n)
    return m.group(2) if m else n


# ─────────────────────────────── dates ─────────────────────────────────────

PRESENT = re.compile(r"present|current|till|now|ongoing|date", re.I)


def parse_truth_date(s):
    """-> ('present',) | (year, month|None) | None"""
    s = str(s or "").strip()
    if not s:
        return None
    if PRESENT.search(s):
        return ("present",)
    m = re.search(r"(19|20)\d{2}", s)
    if not m:
        return None
    year = int(m.group(0))
    mm = re.search(r"(19|20)\d{2}-(\d{1,2})", s) or re.search(r"(\d{1,2})[/.-](19|20)\d{2}", s)
    month = None
    if mm:
        g = mm.group(2) if mm.re.pattern.startswith("(19") else mm.group(1)
        month = int(g) if 1 <= int(g) <= 12 else None
    return (year, month)


def ts_to_ym(ts):
    try:
        d = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc) if ts else None
    except Exception:
        return None
    return (d.year, d.month) if d else None


def date_score(truth, parsed) -> float:
    """1 = right, 0.5 = same year / fabricated precision, 0 = wrong."""
    if truth is None and parsed is None:
        return 1.0
    if truth is None:
        return 0.5                      # parser supplied a date the resume doesn't have
    if parsed is None:
        return 0.0
    if truth == ("present",) or parsed == ("present",):
        return 1.0 if truth == parsed else 0.0
    if truth[0] != parsed[0]:
        return 0.0
    if truth[1] is None or truth[1] == parsed[1]:
        return 1.0
    return 0.5


def fmt_date(d):
    if d is None:
        return ""
    if d == ("present",):
        return "Present"
    return f"{d[0]}-{d[1]:02d}" if d[1] else str(d[0])


# ─────────────────────────────── field scorers ─────────────────────────────

def scalar_status(truth, parsed, kind="text", allow_unverified=False):
    t, p = clean(truth), clean(parsed)
    if not t and not p:
        return "Correct", "empty in resume and in output"
    if not t:
        return ("Unverified extra" if allow_unverified else "Hallucinated"), "value not in resume"
    if not p:
        return "Missing", ""
    if kind == "email":
        return ("Correct", "") if t.lower() == p.lower() else ("Wrong", "")
    if kind == "phone":
        return ("Correct", "") if phones_same(t, p) else ("Wrong", "")
    if kind == "postal":
        return ("Correct", "") if norm(t).replace(" ", "") == norm(p).replace(" ", "") else ("Wrong", "")
    if kind == "linkedin":
        return ("Correct", "") if canon_linkedin(t) == canon_linkedin(p) else ("Wrong", "")
    if kind in ("country", "state", "city"):
        ct, cp = canon_place(t, kind), canon_place(p, kind)
        if ct == cp or sim(ct, cp) >= 0.85:
            return "Correct", ""
        if ct and cp and (ct in cp or cp in ct):
            return "Partial", ""
        return "Wrong", ""
    s = sim(t, p)
    if s >= 0.85:
        return "Correct", ""
    if s >= 0.6:
        return "Partial", f"similarity {s:.2f}"
    return "Wrong", f"similarity {s:.2f}"


def alternate_contact(kind, truth_v, parsed_v, resume_text):
    """Parsed email/phone != key value: is it the candidate's other email/phone?"""
    lines = resume_text.splitlines()
    if kind == "email":
        key = lambda s: s.lower()
        want, have = key(parsed_v), key(truth_v)
    else:
        key = digits
        want, have = digits(parsed_v)[-8:], digits(truth_v)[-7:]
    if not want:
        return "Wrong", ""
    header = [l for l in lines if l.strip()][:25]
    for l in lines:
        if want in key(l) and (have in key(l) or l in header):
            return "Correct", f"candidate's alternate {kind} (in contact block)"
    if any(want in key(l) for l in lines):
        return "Partial", f"{kind} is in the resume but not the candidate's primary contact"
    return "Wrong", f"{kind} not found in resume"


def name_status(truth, parsed):
    tf, pf = truth.get("fullName", ""), parsed.get("fullName", "")
    if norm(tf) and norm(tf).replace(" ", "") == norm(pf).replace(" ", ""):
        st, note = "Correct", "" if norm(tf) == norm(pf) else "same letters, spacing differs"
    else:
        st, note = scalar_status(tf, pf)
    # right name but first/last stored the wrong way round (truth first == parsed last)?
    tfn, tln = truth.get("firstName", ""), truth.get("lastName", "")
    pfn, pln = parsed.get("firstName", ""), parsed.get("lastName", "")
    if st == "Correct" and norm(tfn) and norm(tln) and norm(tfn) != norm(tln) \
            and sim(tfn, pln) >= 0.9 and sim(tln, pfn) >= 0.9 and sim(tfn, pfn) < 0.9:
        return "Partial", "first/last name swapped"
    return st, note


def years_as_of_resume(truth, resume_text):
    """Other valid readings of total experience, besides the key's span-to-today figure:
    span to the resume's own date (latest year printed) and summed job durations
    (overlaps merged). Returns the list of alternative values."""
    years_in_text = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", resume_text) if int(y) <= 2026]
    resume_year = max(years_in_text) if years_in_text else 2026
    spans = []
    for j in truth.get("workHistory", []) or []:
        st, en = parse_truth_date(j.get("start")), parse_truth_date(j.get("end"))
        if not st or st == ("present",):
            continue
        a = st[0] * 12 + (st[1] or 1) - 1
        if en == ("present",):
            b = resume_year * 12 + 11
        elif en:
            b = en[0] * 12 + (en[1] or 12) - 1
        else:
            continue
        if b >= a:
            spans.append((a, b))
    if not spans:
        return []
    spans.sort()
    merged, total = [list(spans[0])], 0
    for a, b in spans[1:]:
        if a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    total = sum(b - a + 1 for a, b in merged) / 12
    span_resume = (max(b for _, b in spans) - min(a for a, _ in spans) + 1) / 12
    return [round(span_resume), round(total)]


def years_status(truth_v, parsed, alt=None):
    t = str(truth_v if truth_v is not None else "").strip()
    p = str(parsed or "").strip()
    try:
        tv = float(t) if t else None
    except ValueError:
        tv = None
    try:
        pv = float(p) if p else None
    except ValueError:
        pv = None
    if tv is None and not pv:
        return "Correct", "no dated experience"
    if tv is None:
        return "Unverified extra", "truth has no total"
    if pv is None:
        return ("Correct", "") if tv < 1 else ("Missing", "")
    vals = [tv] + list(alt or [])
    lo, hi = min(vals), max(vals)
    rng = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
    d = 0 if lo <= pv <= hi else min(abs(pv - lo), abs(pv - hi))
    note = f"resume supports {rng} yrs / parsed {pv:g}"
    if d <= 1:
        return "Correct", note
    if d <= 2:
        return "Partial", note
    return "Wrong", note


def qualification_status(truth, parsed_q):
    t = truth.get("highestQualification", "")
    parsed_q = clean(parsed_q)
    st, note = scalar_status(t, parsed_q)
    if st in ("Wrong", "Partial") and parsed_q:
        others = [e.get("degree", "") for e in truth.get("education", [])]
        if st == "Wrong" and any(sim(o, parsed_q) >= 0.7 for o in others):
            return "Partial", "a real degree, but not the highest"
    return st, note


_STOP = {"and", "of", "the", "in", "for", "with", "to", "an", "on", "at", "by", "skills", "skill"}


def _stem(w):
    for suf in ("ing", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def content_toks(s):
    return {_stem(w) for w in norm(s).split() if len(w) > 1 and w not in _STOP}


def skill_match(a, b):
    """Same skill? exact-ish string, or the shorter one's key words are inside the longer one."""
    if sim(a, b) >= 0.8:
        return True
    ta, tb = content_toks(a), content_toks(b)
    return bool(ta and tb) and len(ta & tb) / min(len(ta), len(tb)) >= 0.66


def split_skills(v):
    return [s.strip() for s in re.split(r"[,;|\n]", str(v or "")) if s.strip()]


def skills_score(truth, parsed_str, resume_text):
    P = split_skills(parsed_str)
    T = truth.get("skills", []) or []
    K = truth.get("keySkills", []) or T[:10]
    flat = norm(resume_text)
    text_toks = {_stem(w) for w in flat.split()}
    if not P and not T:
        return "Correct", 1.0, "no skills in resume or output", {}

    def grounded(s):
        ns, ts = norm(s), content_toks(s)
        return bool(ns) and (ns in flat or (ts and ts <= text_toks)
                             or any(skill_match(s, t) for t in T))

    def captured(k):
        return any(skill_match(k, p) for p in P)

    g = [s for s in P if grounded(s)]
    precision = len(g) / len(P) if P else 0.0
    recall = sum(captured(k) for k in K) / len(K) if K else (1.0 if not P else 0.5)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    status = "Correct" if f1 >= 0.75 else ("Partial" if f1 >= 0.4 else "Wrong")
    if not P:
        status = "Missing"
    detail = {"parsed": len(P), "grounded": len(g), "precision": round(precision, 2),
              "key_skills": len(K), "key_captured": round(recall * len(K)), "recall": round(recall, 2),
              "ungrounded_examples": [s for s in P if s not in g][:6]}
    note = (f"{len(g)}/{len(P)} extracted skills are in the resume; "
            f"{detail['key_captured']}/{len(K)} key skills captured")
    return status, round(f1, 3), note, detail


def match_entries(truth_list, parsed_list, key_fn, threshold=0.55):
    """Greedy best-pair matching. Returns list of (ti, pi, sim) and unmatched sets."""
    pairs = []
    for ti, t in enumerate(truth_list):
        for pi, p in enumerate(parsed_list):
            s = key_fn(t, p)
            if s >= threshold:
                pairs.append((s, ti, pi))
    pairs.sort(reverse=True)
    used_t, used_p, matches = set(), set(), []
    for s, ti, pi in pairs:
        if ti in used_t or pi in used_p:
            continue
        used_t.add(ti); used_p.add(pi)
        matches.append((ti, pi, s))
    return matches, set(range(len(truth_list))) - used_t, set(range(len(parsed_list))) - used_p


def _date_sim(t, p):
    return (date_score(parse_truth_date(t.get("start")), p["start"]) +
            date_score(parse_truth_date(t.get("end")), p["end"])) / 2


def work_key(t, p):
    """Identity (company/title) decides whether it's the same job; dates break ties between
    look-alike entries (e.g. several contracts with the same rank at the same line)."""
    tc, tt = t.get("company", ""), t.get("title", "")
    pc, pt = p.get("company", ""), p.get("title", "")
    ident = 0.6 * sim(tc, pc) + 0.4 * sim(tt, pt) if norm(tc) else sim(tt, pt) * 0.9
    ds = _date_sim(t, p)
    # same rank + exact dated period, but vessel vs employer recorded as company
    if ident < 0.55 and ds == 1 and parse_truth_date(t.get("start")) and sim(tt, pt) >= 0.6:
        ident = 0.56
    return ident + 0.15 * ds if ident >= 0.55 else ident


def edu_key(t, p):
    ti, td = t.get("institute", ""), (t.get("degree", "") + " " + t.get("specialization", ""))
    pi, pd = p.get("institute", ""), (p.get("degree", "") + " " + p.get("specialization", ""))
    ident = 0.55 * sim(ti, pi) + 0.45 * sim(td, pd) if norm(ti) else sim(td, pd) * 0.9
    return ident + 0.15 * _date_sim(t, p) if ident >= 0.55 else ident


def entries_score(truth_list, parsed_list, key_fn, label):
    nt, np_ = len(truth_list), len(parsed_list)
    if nt == 0 and np_ == 0:
        return "Correct", 1.0, f"no {label} in resume or output", {"rows": []}
    if np_ == 0:
        return "Missing", 0.0, f"0 of {nt} {label} captured", {"rows": [], "truth": nt, "parsed": 0}
    matches, missed, extra = match_entries(truth_list, parsed_list, key_fn)
    q_sum, date_errs, rows = 0.0, 0, []
    for ti, pi, s in sorted(matches):
        t, p = truth_list[ti], parsed_list[pi]
        ds = (date_score(parse_truth_date(t.get("start")), p["start"]) +
              date_score(parse_truth_date(t.get("end")), p["end"])) / 2
        if ds < 1:
            date_errs += 1
        q_sum += 0.5 + 0.5 * ds
        rows.append({"truth": t, "parsed": p, "date_score": ds})
    recall = q_sum / nt if nt else 0.0
    precision = len(matches) / np_ if np_ else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    if nt == 0:
        f1 = 0.0
    status = "Correct" if f1 >= 0.9 else ("Partial" if f1 >= 0.5 else "Wrong")
    note = (f"{len(matches)}/{nt} {label} found, {len(missed)} missed, "
            f"{len(extra)} extra/duplicate, {date_errs} with date errors")
    detail = {"truth": nt, "parsed": np_, "matched": len(matches), "missed": len(missed),
              "extra": len(extra), "date_errors": date_errs,
              "missed_items": [truth_list[i] for i in sorted(missed)],
              "extra_items": [parsed_list[i] for i in sorted(extra)], "rows": rows}
    return status, round(f1, 3), note, detail


# ─────────────────────────────── parsed record loading ─────────────────────

def parsed_record(env, name, state_fields):
    raw = json.loads((RAW_DIR[env] / f"{name}.json").read_text(encoding="utf-8"))
    work = []
    for w in raw.get("work", []):
        end = ("present",) if w.get("is_currently_working") else ts_to_ym(w.get("work_end_date"))
        work.append({"title": w.get("title", "") or "", "company": w.get("work_company_name", "") or "",
                     "start": ts_to_ym(w.get("work_start_date")), "end": end})
    edu = []
    for e in raw.get("education", []):
        edu.append({"degree": e.get("educational_qualification", "") or "",
                    "specialization": e.get("educational_specialization", "") or "",
                    "institute": e.get("institute_name", "") or "",
                    "start": ts_to_ym(e.get("education_start_date")),
                    "end": ts_to_ym(e.get("education_end_date"))})
    return {**state_fields, "work": work, "edu": edu}


def score_env(truth, rec, resume_text):
    out = {}

    def put(field, status, score=None, note="", detail=None, truth_v="", parsed_v=""):
        out[field] = {"status": status,
                      "score": STATUS_SCORE[status] if score is None else score,
                      "note": note, "truth": truth_v, "parsed": parsed_v, "detail": detail or {}}

    st, n = name_status(truth, rec)
    put("fullName", st, note=n, truth_v=truth.get("fullName", ""), parsed_v=rec.get("fullName", ""))
    for f, kind in (("email", "email"), ("phone", "phone"), ("postalCode", "postal"), ("linkedin", "linkedin")):
        st, n = scalar_status(truth.get(f, ""), rec.get(f, ""), kind, allow_unverified=(f == "postalCode"))
        if st == "Wrong" and kind in ("email", "phone"):
            st, n = alternate_contact(kind, truth.get(f, ""), rec.get(f, ""), resume_text)
        put(f, st, note=n, truth_v=truth.get(f, ""), parsed_v=rec.get(f, ""))
    for f in ("city", "state", "country"):
        st, n = scalar_status(truth.get(f, ""), rec.get(f, ""), f, allow_unverified=(f != "city"))
        put(f, st, note=n, truth_v=truth.get(f, ""), parsed_v=rec.get(f, ""))
    for f in ("currentTitle", "currentOrg"):
        st, n = scalar_status(truth.get(f, ""), rec.get(f, ""))
        put(f, st, note=n, truth_v=truth.get(f, ""), parsed_v=rec.get(f, ""))
    st, n = years_status(truth.get("workExpYears"), rec.get("workExpYears"),
                         years_as_of_resume(truth, resume_text))
    put("workExpYears", st, note=n, truth_v=str(truth.get("workExpYears", "")), parsed_v=rec.get("workExpYears", ""))
    st, n = qualification_status(truth, rec.get("qualification", ""))
    put("qualification", st, note=n, truth_v=truth.get("highestQualification", ""), parsed_v=rec.get("qualification", ""))
    st, sc, n, d = skills_score(truth, rec.get("skills", ""), resume_text)
    put("skills", st, sc, n, d, truth_v=", ".join(truth.get("keySkills", []) or []), parsed_v=rec.get("skills", ""))
    st, sc, n, d = entries_score(truth.get("workHistory", []) or [], rec["work"], work_key, "jobs")
    put("workHistory", st, sc, n, d, truth_v=f"{len(truth.get('workHistory', []) or [])} jobs",
        parsed_v=f"{len(rec['work'])} entries")
    st, sc, n, d = entries_score(truth.get("education", []) or [], rec["edu"], edu_key, "degrees")
    put("education", st, sc, n, d, truth_v=f"{len(truth.get('education', []) or [])} degrees",
        parsed_v=f"{len(rec['edu'])} entries")

    total = sum(WEIGHTS[f] * out[f]["score"] for f in FIELDS) / sum(WEIGHTS.values()) * 100
    return out, round(total, 1)


def why(res):
    s, d = res["fields"]["Staging"], res["fields"]["Dev"]
    deltas = sorted(((WEIGHTS[f] * (d[f]["score"] - s[f]["score"]), f) for f in FIELDS),
                    key=lambda x: -abs(x[0]))
    parts = []
    for delta, f in deltas[:3]:
        if abs(delta) < 1e-9:
            continue
        better, worse = ("Dev", "Staging") if delta > 0 else ("Staging", "Dev")
        b, w = res["fields"][better][f], res["fields"][worse][f]
        parts.append(f"{f}: {better} {b['status']}"
                     f"{' (' + b['note'] + ')' if b['note'] and f in ('workHistory', 'education', 'skills', 'workExpYears') else ''}"
                     f" vs {worse} {w['status']}"
                     f"{' (' + w['note'] + ')' if w['note'] and f in ('workHistory', 'education', 'skills', 'workExpYears') else ''}")
    return "; ".join(parts) or "Both outputs score identically against the resume"


def load_overrides():
    ov, checked = {}, {}
    for f in sorted(glob.glob(str(VERIFY_DIR / "*.json"))):
        for o in json.loads(Path(f).read_text(encoding="utf-8")):
            if o.get("field") == "_checked":
                checked[o["resume"]] = o.get("reason", "")
            elif o.get("env") in ENVS and o.get("field") in WEIGHTS and o.get("correct_status") in STATUS_SCORE:
                ov[(o["resume"], o["field"], o["env"])] = o
    return ov, checked


def apply_overrides(name, fields, overrides):
    n = 0
    for f in FIELDS:
        o = overrides.get((name, f, fields["_env"]))
        if not o:
            continue
        cur = fields[f]
        cur["scorer_status"] = cur["status"]
        cur["status"] = o["correct_status"]
        cur["score"] = float(o["score"]) if o.get("score") is not None else STATUS_SCORE[o["correct_status"]]
        cur["note"] = f"[verified vs resume] {o.get('reason', '')}"
        n += 1
    return n


def main():
    overrides, checked = load_overrides()
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    truth = {}
    for f in sorted(glob.glob(str(TRUTH_DIR / "*.json"))):
        for t in json.loads(Path(f).read_text(encoding="utf-8")):
            truth[t["resume"]] = t
    results = {}
    for name, t in sorted(truth.items()):
        if name not in state:
            continue
        text = (TEXT_DIR / f"{name}.txt").read_text(encoding="utf-8", errors="ignore")
        res = {"resume": name, "fields": {}, "score": {}}
        res["overrides"] = 0
        for env in ENVS:
            rec = parsed_record(env, name, state[name][env]["fields"])
            fields, _ = score_env(t, rec, text)
            fields["_env"] = env
            res["overrides"] += apply_overrides(name, fields, overrides)
            del fields["_env"]
            res["fields"][env] = fields
            res["score"][env] = round(sum(WEIGHTS[f] * fields[f]["score"] for f in FIELDS)
                                      / sum(WEIGHTS.values()) * 100, 1)
        res["verified"] = checked.get(name, "")
        gap = res["score"]["Dev"] - res["score"]["Staging"]
        res["winner"] = "Dev" if gap >= WIN_MARGIN else ("Staging" if gap <= -WIN_MARGIN else "Tie")
        res["gap"] = round(gap, 1)
        res["why"] = why(res)
        res["truth_notes"] = t.get("notes", "")
        results[name] = res
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    w = [r["winner"] for r in results.values()]
    print(f"scored {len(results)} | Dev {w.count('Dev')} | Staging {w.count('Staging')} | Tie {w.count('Tie')}"
          f" | overrides applied {sum(r['overrides'] for r in results.values())}"
          f" | resumes verified {sum(1 for r in results.values() if r['verified'])}")


if __name__ == "__main__":
    main()
