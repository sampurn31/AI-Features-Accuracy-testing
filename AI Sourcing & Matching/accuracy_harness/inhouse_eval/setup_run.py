"""
Wire the sourcing accuracy test into the inhouse-evaluator and launch one llm_judge run.

Reproduces the 300-variation scale run's judge audit inside the evaluator: each dataset row
is one candidate scoring task, the models under test play the AI Sourcing scoring engine,
and the evaluator's judge audits what they produced with the per-criterion rubric.

    # dry run — creates nothing, prints what it would send
    python3 setup_run.py --models gemini/gemini-2.5-flash-lite,gpt-4.1-mini --dry-run

    # for real
    python3 setup_run.py --models gemini/gemini-2.5-flash-lite,gpt-4.1-mini

Requires the evaluator backend running (uvicorn main:app --port 8000 from backend/).
Re-uses the Feature and dataset by name if they already exist, so it is safe to re-run.
"""
import argparse, json, os, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
SCORING_PROMPT = os.path.join(HARNESS, "prompts", "model_scoring_prompt.txt")
DATASET = os.path.join(HERE, "sourcing_llm_judge.jsonl")
FEATURE_NAME = "AI Sourcing — Candidate Scoring"


def call(base, method, path, body=None, raw=None, ctype=None):
    url = base.rstrip("/") + path
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype or "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed ({e.code}): {e.read().decode()[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach the evaluator at {base} — is the backend running?\n  {e}")


def multipart(fields, files):
    """Minimal multipart/form-data body so this stays dependency-free."""
    b = "----inhouseeval7f3a2b"
    out = b""
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (name, blob) in files.items():
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + blob + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="comma-separated models under test")
    ap.add_argument("--judge", default="gpt-5-mini")
    ap.add_argument("--judge-effort", default="low", choices=["", "low", "medium", "high"],
                    help="matches the harness run's effort=low")
    ap.add_argument("--judge-temp", type=float, default=0.1)
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="scoring should be near-deterministic; harness scored at low temp")
    ap.add_argument("--concurrency", type=int, default=5, help="rows in flight; 0 = all at once")
    ap.add_argument("--check-determinism", action="store_true",
                    help="also re-run a sample N times to measure scoring stability")
    ap.add_argument("--name", default="AI Sourcing accuracy — llm_judge")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    if not os.path.exists(DATASET):
        sys.exit(f"missing {DATASET} — run build_sourcing_dataset.py first")
    judge_system = open(os.path.join(HERE, "judge_system_prompt.txt")).read().strip()
    judge_criteria = open(os.path.join(HERE, "judge_criteria.txt")).read().strip()
    scoring_system = open(SCORING_PROMPT).read().strip()
    rows = sum(1 for line in open(DATASET) if line.strip())

    print(f"models under test : {', '.join(models)}")
    print(f"judge             : {a.judge} (effort {a.judge_effort or 'default'}, temp {a.judge_temp})")
    print(f"dataset           : {rows} rows · {DATASET}")
    print(f"calls             : {rows * len(models)} scoring + up to {rows * len(models)} judge")
    if a.dry_run:
        print("\n--dry-run: nothing created.")
        return

    user = (call(a.base, "GET", "/users") or [{}])[0].get("id", "")

    features = call(a.base, "GET", "/features") or []
    feature = next((f for f in features if f["name"] == FEATURE_NAME), None)
    if feature:
        feature = call(a.base, "PUT", f"/features/{feature['id']}",
                       {"system_prompt": scoring_system, "default_technique": "llm_judge",
                        "default_eval_prompt": judge_system, "user_id": user})
        print(f"feature           : reused {feature['id']} (prompt version {feature.get('system_prompt_version')})")
    else:
        feature = call(a.base, "POST", "/features",
                       {"name": FEATURE_NAME,
                        "description": "Per-criterion candidate scoring, replayed from the "
                                       "300-variation sourcing scale run (20260723-full).",
                        "system_prompt": scoring_system, "default_technique": "llm_judge",
                        "default_eval_prompt": judge_system, "user_id": user})
        print(f"feature           : created {feature['id']}")

    body, ctype = multipart({"feature_id": feature["id"], "user_id": user},
                            {"file": ("sourcing_llm_judge.jsonl", open(DATASET, "rb").read())})
    ds = call(a.base, "POST", "/datasets", raw=body, ctype=ctype)
    print(f"dataset           : {ds['id']} · {ds['row_count']} rows")

    run = call(a.base, "POST", "/evaluations", {
        "name": a.name,
        "dataset_id": ds["id"],
        "selected_models": models,
        "evaluator_model": a.judge,
        "temperature": a.temperature,
        "selected_model_settings": {m: {"temperature": a.temperature, "reasoning_effort": ""} for m in models},
        "evaluator_temperature": a.judge_temp,
        "evaluator_reasoning_effort": a.judge_effort,
        "evaluator_system_prompt": judge_system,
        "evaluation_mode": "llm_judge",
        "eval_criteria": judge_criteria,
        "reference_model": "",
        "feature_id": feature["id"],
        "user_id": user,
        "concurrency": a.concurrency,
        "check_determinism": a.check_determinism,
    })
    print(f"\nrun started       : {run['id']} ({run['status']})")
    print(f"watch it          : {a.base}/evaluations/{run['id']}  ·  UI: http://localhost:3000/evaluations/{run['id']}")


if __name__ == "__main__":
    main()
