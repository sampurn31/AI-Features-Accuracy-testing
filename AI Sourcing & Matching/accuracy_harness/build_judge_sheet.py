"""One job, 100 candidates: the exact prompts sent to score and to judge."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import config, llm
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

QID = sys.argv[1] if len(sys.argv) > 1 else "sales-bd-bengaluru"
SRC = "output/sourcing_eval_evalopus.jsonl"
MAXCELL = 32000
INK="1C2733"; HEAD=PatternFill("solid",fgColor="E8EFFC"); ALT=PatternFill("solid",fgColor="F7F8FA")

recs=[json.loads(l) for l in open(SRC) if '"cand"' in l]
recs=[r for r in recs if r["qid"]==QID]
recs.sort(key=lambda r:(r.get("page") or 0))
rubric=open(os.path.join(config.PROMPTS_DIR, "sourcing_eval_rubric.md")).read().rstrip()
schema=json.dumps(llm.SOURCING_EVAL_SCHEMA, indent=2)
query=recs[0]["query"]; criteria=recs[0]["criteria"]

def user_msg(r):
    """Byte-for-byte the user turn llm.judge_sourcing_eval built for this candidate."""
    cand=dict(r.get("profile") or {})
    cand["matchScore"]=r.get("api_overall"); cand["matchLabel"]=r.get("api_label")
    cand["matchAnalysis"]=r.get("api_analysis") or []
    crit_lines="\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in criteria)
    api={"matchScore":cand.get("matchScore"),"matchLabel":cand.get("matchLabel"),
         "perCriterion":[{"criterion":a.get("criterionText"),"weight":a.get("weight"),
                          "score":a.get("score"),"reasoning":a.get("reasoning")}
                         for a in cand["matchAnalysis"]]}
    return "".join([
        "SEARCH QUERY:\n"+(query or ""),
        "\n\nWEIGHTED CRITERIA:\n"+crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):\n"
        + json.dumps(llm._profile_for_judge(cand), indent=2, ensure_ascii=False)[:16000],
        "\n\nOUR API's SCORING (per-criterion `score` 0-100; overall matchScore = their weighted avg):\n"
        + json.dumps(api, indent=2, ensure_ascii=False)[:6000],
        "\n\nDo three things per the rubric: (1) score this candidate yourself — overall 0-100 and "
        "each criterion 0-100 — grounded ONLY in the profile; (2) judge whether OUR API's scores are "
        "good enough (per criterion: agree / api_too_high / api_too_low; overall: yes/borderline/no); "
        "(3) decide if the candidate is RELEVANT to the search query (yes/partial/no) and why. Return "
        "JSON only; cite specific profile evidence.",
    ])

def cut(t):
    t="" if t is None else str(t)
    return t if len(t)<=MAXCELL else t[:MAXCELL-60]+"\n…[truncated for Excel's 32,767-char cell limit]"

wb=Workbook(); wb.remove(wb.active)

def sheet(name, cols, widths):
    ws=wb.create_sheet(name); ws.append(cols)
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
    for c in ws[1]: c.font=Font(bold=True,size=9,color=INK); c.fill=HEAD; c.alignment=Alignment(wrap_text=True,vertical="center")
    ws.freeze_panes="A2"; ws.auto_filter.ref=f"A1:{get_column_letter(len(cols))}1"
    return ws

# 1. README
ws=sheet("README",["Field","Value"],[34,120])
for k,v in [
 ("What this is","One job (one search query), its top 100 candidates, and the complete prompts behind every LLM call made for them."),
 ("Job / query id",QID),("Query sent to the product",query),
 ("Run","evalopus · prod · 2026-08-06 · run_sourcing_eval.py"),
 ("Candidates",f"{len(recs)} (pages 1-4 of the scored top-100)"),
 ("Step 1 — scoring","The PRODUCT scored each candidate server-side (Gemini Flash Lite at the time). We receive matchScore + matchLabel + per-criterion matchAnalysis. The product's own system prompt runs inside Neptune and is not exposed on the response, so it is not reproducible from the client — see the 'Product scoring prompt' sheet."),
 ("Step 2 — judging","For EACH candidate one Sonnet-5 call re-scored the candidate independently, judged whether the product's score was good enough, and decided relevance. That is the call reproduced in full here."),
 ("Judge model",f"{config.MODEL} · effort {config.JUDGE_EFFORT} · max_tokens {config.JUDGE_MAX_TOKENS} · forced JSON"),
 ("Judge system prompt",f"prompts/sourcing_eval_rubric.md ({len(rubric.splitlines())} lines) — identical on all {len(recs)} calls"),
 ("Criteria source",recs[0].get("criteria_source")),
 ("Columns per candidate","System Prompt = the rubric. User Prompt = query + criteria + profile + the product's scoring. Prompt given to LLM as judge = the two concatenated exactly as the API received them."),
 ("Cell limit","Excel caps a cell at 32,767 chars; the few longest prompts are truncated with a marker. The untruncated text is in the JSONL checkpoint."),
]:
    ws.append([k,cut(v)])
    for c in ws[ws.max_row]: c.alignment=Alignment(wrap_text=True,vertical="top"); c.font=Font(size=9)

# 2. The job
ws=sheet("The job",["Item","Value"],[26,130])
ws.append(["Query (as sent)",query])
for i,c in enumerate(criteria,1): ws.append([f"Criterion {i}", f"{c.get('text')}  (weight {c.get('weight')})"])
for row in ws.iter_rows(min_row=2):
    for c in row: c.alignment=Alignment(wrap_text=True,vertical="top"); c.font=Font(size=9)

# 3. the 100 judge calls
cols=["#","Page","Candidate","LinkedIn","System Prompt","User Prompt",
      "Prompt given to LLM as judge (system + user, as sent)",
      "PRODUCT score (LLM-generated)","PRODUCT label","PRODUCT per-criterion (its own reasoning)",
      "JUDGE score","Gap (product - judge)","Judge: is product score good enough?","Judge note",
      "Judge: relevant?","Judge relevance reason","Judge per-criterion verdicts"]
ws=sheet(f"100 candidates - {QID}"[:31],cols,[5,6,26,40,60,70,70,12,14,60,10,10,14,50,10,50,70])
for i,r in enumerate(recs,1):
    j=r.get("judge") or {}
    um=user_msg(r)
    full="[SYSTEM]\n"+rubric+"\n\n[USER]\n"+um
    ap_=r.get("api_overall"); jp=j.get("llm_overall_score")
    gap=(ap_-jp) if isinstance(ap_,(int,float)) and isinstance(jp,(int,float)) else ""
    ws.append([i,r.get("page"),r.get("name"),r.get("linkedin"),
               cut(rubric),cut(um),cut(full),
               ap_,r.get("api_label"),
               cut(json.dumps(r.get("api_analysis") or [],indent=1,ensure_ascii=False)),
               jp,gap,j.get("api_overall_good_enough") or ("ERROR" if j.get("_error") else ""),
               cut(j.get("api_overall_note") or j.get("_error","")),
               j.get("relevant"),cut(j.get("relevance_reason")),
               cut(json.dumps(j.get("per_criterion") or [],indent=1,ensure_ascii=False))])
    for c in ws[ws.max_row]:
        c.alignment=Alignment(wrap_text=True,vertical="top"); c.font=Font(size=9)
        if i%2: c.fill=ALT

# 4/5/6 reference sheets
for nm,txt in [("Judge system prompt",rubric),("Judge output schema",schema),
               ("Product scoring prompt",
                "The product scores candidates inside Neptune (server-side). The response returns only the\n"
                "RESULT of that scoring — matchScore, matchLabel and per-criterion matchAnalysis — never the\n"
                "prompt that produced it, so it cannot be reconstructed from the client the way the judge call can.\n\n"
                "The closest artefacts we hold:\n"
                "  • prompts/model_scoring_prompt.txt — the scoring prompt WE gave challenger models in the\n"
                "    model bake-off, written to mirror the product's scoring task.\n"
                "  • prompts/neptune_global_database_config_system_prompt.txt and the criteria-to-filters\n"
                "    prompts — verbatim production Neptune prompts for the CONFIG stage (questions, criteria,\n"
                "    filters). These are the config prompts, not the candidate-scoring prompt.\n\n"
                "To put the real scoring system prompt in this workbook, ask engineering for the Neptune\n"
                "candidate-scoring constant (the one behind /v1/ai-sourcing-v3/search scoring).")]:
    w=wb.create_sheet(nm[:31]); w.column_dimensions["A"].width=140
    for k,line in enumerate(txt.split("\n"),1):
        w.cell(row=k,column=1,value=line).font=Font(size=9,name="Menlo" if nm!="Product scoring prompt" else None)
        w.cell(row=k,column=1).alignment=Alignment(wrap_text=True,vertical="top")

out=f"output/LLM_judge_prompts_{QID}_100_candidates.xlsx"
wb.save(out)
print(f"{out}\n{len(recs)} candidates · sheets: {', '.join(wb.sheetnames)}")
print(f"longest user prompt {max(len(user_msg(r)) for r in recs):,} chars · rubric {len(rubric):,} chars")
