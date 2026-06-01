#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
injection_preview.py — Phase 1 Preview Script
----------------------------------------------
Purpose : Validate LLM-generated custom field injections BEFORE running the
          full pipeline.  Does NOT re-parse, validate accuracy, or compare.

Flow:
  1. Parse sample resumes via Staging → extract resumetext
  2. For each (resume × custom field): call LLM → snippet + value
  3. Inject all snippets at a random position in the resume text
  4. Save outputs for human inspection:
       preview_output/<resume_id>_modified.txt   — readable text file
       preview_output/injection_preview.json     — structured record per resume

Run:
  python3 injection_preview.py
"""

import os, json, time, random, re, logging, requests
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

from custom_field_pipeline import (
    GROQ_MODEL,
    BASE_URL_TRACK_A,
    EMAIL_TRACK_A,
    PASSWORD_TRACK_A,
    CUSTOM_FIELDS,
    get_token,
    get_presigned_url,
    upload_pdf,
    call_parse_api,
    extract_resume_text,
    pre_scan_existing_values,
    llm_generate_snippet,
    build_injection_block,
    inject_at_random_position,
)

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SAMPLE_FOLDER  = Path(__file__).parent / "Sample"
OUTPUT_DIR     = Path("preview_output")
PREVIEW_JSON   = OUTPUT_DIR / "injection_preview.json"
TXT_OUTPUT_DIR = OUTPUT_DIR / "txt"

os.makedirs("logs", exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
TXT_OUTPUT_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename="logs/injection_preview.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

def parse_samples(token: str) -> list:
    files = sorted(
        [f for f in SAMPLE_FOLDER.iterdir() if f.suffix.lower() in (".pdf", ".doc", ".docx")],
        key=lambda f: f.name,
    )
    if not files:
        raise FileNotFoundError(f"No resume files found in {SAMPLE_FOLDER}")

    print(f"\n[Step 1] Parsing {len(files)} sample resume(s) via Track A ({BASE_URL_TRACK_A}) ...")
    parsed = []

    for idx, resume_path in enumerate(files, start=1):
        rid = f"S{idx:02d}"
        print(f"  [{rid}] {resume_path.name} ...", end=" ", flush=True)
        try:
            presign      = get_presigned_url(BASE_URL_TRACK_A, resume_path.name, token)
            upload_pdf(presign["preSignedUrl"], resume_path)
            result       = call_parse_api(BASE_URL_TRACK_A, token, presign["key"], resume_path.name)
            resume_text  = extract_resume_text(result)

            if not resume_text or resume_text.strip() == "":
                print("WARN – empty resumetext")
                log.warning(f"[Preview] {rid} returned empty resumetext")
            else:
                print(f"OK  ({len(resume_text)} chars)")

            parsed.append({
                "resume_id":   rid,
                "filename":    resume_path.name,
                "resume_text": resume_text,
            })
            log.info(f"[Preview] {rid} parsed, {len(resume_text)} chars")
        except Exception as exc:
            print(f"ERROR – {exc}")
            log.error(f"[Preview] {rid} parse failed: {exc}")
            parsed.append({"resume_id": rid, "filename": resume_path.name,
                           "resume_text": "", "error": str(exc)})
        time.sleep(1)

    return parsed

def generate_snippets(parsed_resumes: list) -> list:
    print(f"\n[Step 2] Generating snippets: "
          f"{len(parsed_resumes)} resume(s) × {len(CUSTOM_FIELDS)} field(s) ...")
    enriched = []

    for record in parsed_resumes:
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")

        if record.get("error") or not resume_text.strip():
            print(f"  [{rid}] Skipped (no resume text)")
            enriched.append({**record, "injected_fields": []})
            continue

        print(f"  [{rid}] Generating {len(CUSTOM_FIELDS)} snippets ...", end=" ", flush=True)
        injected = []

        for field in CUSTOM_FIELDS:
            pre_existing = pre_scan_existing_values(resume_text, field)
            result       = llm_generate_snippet(field, pre_existing)
            snippet      = result.get("snippet", "")
            value        = result.get("value", "")

            injected.append({
                "field_name":   field["name"],
                "field_type":   field["type"],
                "column_index": field["column_index"],
                "value":        value,
                "snippet":      snippet,
                "pre_existing": pre_existing,
            })

        ok_count = sum(1 for f in injected if f["snippet"])
        print(f"{ok_count}/{len(CUSTOM_FIELDS)} OK")
        enriched.append({**record, "injected_fields": injected})
        time.sleep(0.3)

    return enriched

def inject_and_save(enriched: list) -> list:
    print(f"\n[Step 3] Injecting snippets and saving outputs ...")
    results = []

    for record in enriched:
        rid            = record["resume_id"]
        resume_text    = record.get("resume_text", "")
        injected_fields = record.get("injected_fields", [])

        if not resume_text.strip() or not injected_fields:
            print(f"  [{rid}] Skipped (nothing to inject)")
            results.append({**record, "modified_text": resume_text})
            continue

        block         = build_injection_block(injected_fields)
        modified_text = inject_at_random_position(resume_text, block)

        txt_path = TXT_OUTPUT_DIR / f"{rid}_modified.txt"
        txt_path.write_text(modified_text, encoding="utf-8")

        print(f"  [{rid}] Injected {len(injected_fields)} field(s) → {txt_path.name}")
        log.info(f"[Preview] {rid} injected, saved to {txt_path}")
        results.append({**record, "modified_text": modified_text})

    return results

def save_json(results: list) -> None:
    output = []
    for r in results:
        output.append({
            "resume_id":       r["resume_id"],
            "filename":        r["filename"],
            "original_text":   r.get("resume_text", ""),
            "modified_text":   r.get("modified_text", ""),
            "injected_fields": [
                {
                    "field_name":   f["field_name"],
                    "field_type":   f["field_type"],
                    "column_index": f["column_index"],
                    "value":        f["value"],
                    "snippet":      f["snippet"],
                    "pre_existing": f.get("pre_existing", []),
                }
                for f in r.get("injected_fields", [])
            ],
        })
    PREVIEW_JSON.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[Output] JSON saved  → {PREVIEW_JSON}")

def print_preview_summary(results: list) -> None:
    print("\n" + "=" * 65)
    print("INJECTION PREVIEW SUMMARY")
    print("=" * 65)

    for r in results:
        rid      = r["resume_id"]
        filename = r["filename"]
        fields   = r.get("injected_fields", [])
        mod_text = r.get("modified_text", "")

        print(f"\n  {rid}  {filename}")
        print(f"  Modified text length : {len(mod_text)} chars")
        print(f"  Fields injected      : {sum(1 for f in fields if f.get('snippet'))}/{len(fields)}")
        for f in fields:
            snippet_preview = (f["snippet"][:80] + "…") if len(f.get("snippet","")) > 80 else f.get("snippet","—")
            print(f"    [{f['column_index']:02d}] {f['field_name']:<35s}  value={f['value']}")
            print(f"         snippet: {snippet_preview}")

    txt_files = list(TXT_OUTPUT_DIR.glob("*.txt"))
    print(f"\n  .txt files saved : {len(txt_files)}  →  {TXT_OUTPUT_DIR}/")
    print(f"  JSON saved       : {PREVIEW_JSON}")
    print("=" * 65)

def main():
    print("=" * 65)
    print("Custom Field Injection Preview  (Phase 1)")
    print("=" * 65)

    print("\n[Auth] Fetching Track A token ...")
    token = get_token(BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A)
    print("  Track A token OK")

    parsed = parse_samples(token)

    enriched = generate_snippets(parsed)

    results = inject_and_save(enriched)

    save_json(results)

    print_preview_summary(results)


if __name__ == "__main__":
    main()
