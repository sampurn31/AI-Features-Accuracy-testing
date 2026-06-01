#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bulk_resume_parser_logger.py
────────────────────────────────────────────────────────────────────────────────
Simple standalone script to parse resumes and log results.

Purpose:
  - Parse all resumes from /100 resumes/
  - Store standard parsed fields
  - Store parse API response time
  - Export results to Excel: bulk_parse_report.xlsx

No comparison, no validation, no custom fields, no LLM calls.
"""

import datetime
import json
import logging
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

from config import BULK_BASE_URL as BASE_URL, BULK_EMAIL as EMAIL, BULK_PASSWORD as PASSWORD

RESUME_FOLDER = Path(__file__).parent / "100 resumes"
OUTPUT_DIR = Path("bulk_parse_output")
RAW_RESPONSES_DIR = OUTPUT_DIR / "raw_responses"
EXCEL_REPORT = OUTPUT_DIR / "bulk_parse_report.xlsx"

OUTPUT_DIR.mkdir(exist_ok=True)
RAW_RESPONSES_DIR.mkdir(exist_ok=True, parents=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(OUTPUT_DIR / "bulk_parse.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def sanitize_for_excel(value):
    """Remove control characters and illegal chars that openpyxl rejects."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    value = str(value)
    value = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]', '', value)
    return value


def flatten_work_history(work_list: list) -> str:
    """
    Flatten work history to readable string:
    'Title at Company (StartYear - EndYear)'
    """
    if not work_list:
        return ""
    parts = []
    for work in work_list:
        if not isinstance(work, dict):
            continue
        company = work.get("work_company_name", "") or ""
        title = work.get("title", "") or ""
        start_ts = work.get("work_start_date", "")
        end_ts = work.get("work_end_date", "")
        
        start, end = "", "Present"
        try:
            if start_ts and isinstance(start_ts, (int, float)) and start_ts > 0:
                start = datetime.datetime.fromtimestamp(start_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            start = ""
        try:
            if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                end = datetime.datetime.fromtimestamp(end_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            end = "Present"
        
        if title or company:
            parts.append(f"{title} at {company} ({start} - {end})")
    
    return " | ".join(parts)


def flatten_education_history(edu_list: list) -> str:
    """
    Flatten education history to readable string:
    'Degree in Specialization from Institute (StartYear-EndYear)'
    """
    if not edu_list:
        return ""
    edu_strs = []
    for education in edu_list:
        if not isinstance(education, dict):
            continue
        degree = education.get("educational_qualification", "") or ""
        institute = education.get("institute_name", "") or ""
        specialization = education.get("educational_specialization", "") or ""
        start_ts = education.get("education_start_date", "")
        end_ts = education.get("education_end_date", "")
        
        start_year, end_year = "", ""
        try:
            if start_ts and isinstance(start_ts, (int, float)) and start_ts > 0:
                start_year = datetime.datetime.fromtimestamp(start_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            start_year = ""
        try:
            if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                end_year = datetime.datetime.fromtimestamp(end_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            end_year = ""
        
        if degree or institute:
            s = degree
            if specialization:
                s += f" in {specialization}"
            if institute:
                s += f" from {institute}"
            if start_year or end_year:
                s += f" ({start_year or '?'}-{end_year or '?'})"
            edu_strs.append(s)
    
    return " | ".join(edu_strs)


def get_token(base_url: str, email: str, password: str) -> str:
    """Login and return JWT token."""
    resp = requests.post(f"{base_url}/v1/login", data={"email": email, "password": password})
    resp.raise_for_status()
    token = resp.json()["data"]["token"]
    log.info(f"Authenticated as {email}")
    return token


def _auth_headers(token: str) -> dict:
    return {"authorization": f"Bearer {token}", "content-type": "application/json"}


def get_presigned_url(base_url: str, file_name: str, token: str) -> dict:
    """Get S3 presigned URL for uploading."""
    r = requests.get(
        f"{base_url}/v1/get-presigned-url",
        headers={"authorization": f"Bearer {token}"},
        params={"fileName": file_name, "requestType": "put"},
    )
    r.raise_for_status()
    return r.json().get("data", r.json())


def upload_pdf(put_url: str, file_path: Path) -> None:
    """Upload PDF to S3."""
    r = requests.put(
        put_url,
        data=file_path.read_bytes(),
        headers={"Content-Type": "application/pdf"},
    )
    r.raise_for_status()


def call_parse_api(base_url: str, token: str, file_key: str, file_name: str) -> dict:
    """
    Call parse-resume API with onlyParserData=true.
    This returns work/edu history inline on the candidate object.
    """
    payload = {
        "resumeParserData": {
            "filesInfo": {
                "key": file_key,
                "name": file_name,
            }
        },
        "onlyParserData": True,
    }
    r = requests.post(
        f"{base_url}/v1/candidates/parse-resume",
        headers=_auth_headers(token),
        json=payload,
    )
    r.raise_for_status()
    return r.json()


def extract_standard_fields(parse_result: dict) -> dict:
    """Extract standard candidate fields from parse response."""
    data = parse_result.get("data", {})
    c = data.get("candidate", {})

    work_history = c.get("workhistory", []) or []
    edu_history = c.get("educationhistory", []) or []

    return {
        "firstname": c.get("firstname", ""),
        "lastname": c.get("lastname", ""),
        "emailid": c.get("emailid", ""),
        "contactnumber": c.get("contactnumber", ""),
        "city": c.get("city", ""),
        "state": c.get("state", ""),
        "country": c.get("country", ""),
        "address": c.get("address", ""),
        "position": c.get("position", ""),
        "lastorganisation": c.get("lastorganisation", ""),
        "workexpyr": str(c.get("workexpyr", "")),
        "summary": c.get("summary", ""),
        "skill": c.get("skill", ""),
        "languageskills": c.get("languageskills", ""),
        "qualification": c.get("qualification", ""),
        "specialization": c.get("specialization", ""),
        "profilelinkedin": c.get("profilelinkedin", ""),
        "profilegithub": c.get("profilegithub", ""),
        "sovren_document_id": str(c.get("sovren_document_id") or ""),
        "workhistory": work_history,
        "educationhistory": edu_history,
        "work_history_flat": flatten_work_history(work_history),
        "education_history_flat": flatten_education_history(edu_history),
    }


def parse_resume(token: str, resume_path: Path) -> dict:
    """
    Parse a single resume and return result dict with:
      - filename
      - parse_status (success/failed)
      - response_time_ms
      - parse_timestamp
      - standard_fields (if success)
      - error_message (if failed)
      - raw_response_path
    """
    filename = resume_path.name
    result = {
        "filename": filename,
        "parse_status": "failed",
        "response_time_ms": 0,
        "parse_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "error_message": "",
        "raw_response_path": "",
    }

    try:
        presigned = get_presigned_url(BASE_URL, filename, token)
        file_key = presigned["key"]
        put_url = presigned["preSignedUrl"]

        upload_pdf(put_url, resume_path)

        start_time = time.time()
        parse_result = call_parse_api(BASE_URL, token, file_key, filename)
        elapsed_ms = int((time.time() - start_time) * 1000)

        result["response_time_ms"] = elapsed_ms
        result["parse_status"] = "success"

        standard_fields = extract_standard_fields(parse_result)
        result["standard_fields"] = standard_fields

        raw_response_file = RAW_RESPONSES_DIR / f"{filename}.json"
        raw_response_file.write_text(json.dumps(parse_result, indent=2, ensure_ascii=False))
        result["raw_response_path"] = str(raw_response_file.relative_to(OUTPUT_DIR))

        log.info(f"✓ {filename} - {elapsed_ms} ms")

    except Exception as exc:
        result["error_message"] = str(exc)
        log.error(f"✗ {filename} - {exc}")

    return result


def load_results_from_raw_responses() -> list:
    """
    Reconstruct results from saved raw JSON responses (if available).
    Useful when parsing succeeded but Excel generation failed.
    """
    log.info("Attempting to load results from saved raw responses...")
    results = []
    
    if not RAW_RESPONSES_DIR.exists():
        log.warning(f"Raw responses directory not found: {RAW_RESPONSES_DIR}")
        return results
    
    for json_file in sorted(RAW_RESPONSES_DIR.glob("*.json")):
        filename = json_file.name.replace(".json", "")
        try:
            parse_result = json.loads(json_file.read_text(encoding="utf-8"))
            standard_fields = extract_standard_fields(parse_result)
            
            results.append({
                "filename": filename,
                "parse_status": "success",
                "response_time_ms": 0,
                "parse_timestamp": "",
                "error_message": "",
                "raw_response_path": str(json_file.relative_to(OUTPUT_DIR)),
                "standard_fields": standard_fields,
            })
            log.info(f"  Loaded: {filename}")
        except Exception as exc:
            log.warning(f"  Failed to load {filename}: {exc}")
    
    log.info(f"Loaded {len(results)} results from raw responses")
    return results


def generate_excel_report(results: list):
    """Generate clean Excel report from results list."""
    log.info("")
    log.info("Generating Excel report ...")

    rows = []
    for result in results:
        row = {
            "Filename": sanitize_for_excel(result["filename"]),
            "Status": sanitize_for_excel(result["parse_status"]),
            "Response Time (ms)": result["response_time_ms"],
            "Timestamp": sanitize_for_excel(result["parse_timestamp"]),
        }

        if "standard_fields" in result:
            fields = result["standard_fields"]
            row.update({
                "First Name": sanitize_for_excel(fields.get("firstname", "")),
                "Last Name": sanitize_for_excel(fields.get("lastname", "")),
                "Email": sanitize_for_excel(fields.get("emailid", "")),
                "Phone": sanitize_for_excel(fields.get("contactnumber", "")),
                "City": sanitize_for_excel(fields.get("city", "")),
                "State": sanitize_for_excel(fields.get("state", "")),
                "Country": sanitize_for_excel(fields.get("country", "")),
                "Position": sanitize_for_excel(fields.get("position", "")),
                "Last Organisation": sanitize_for_excel(fields.get("lastorganisation", "")),
                "Work Exp (Years)": sanitize_for_excel(fields.get("workexpyr", "")),
                "Skills": sanitize_for_excel(fields.get("skill", "")),
                "Qualification": sanitize_for_excel(fields.get("qualification", "")),
                "Specialization": sanitize_for_excel(fields.get("specialization", "")),
                "LinkedIn": sanitize_for_excel(fields.get("profilelinkedin", "")),
                "GitHub": sanitize_for_excel(fields.get("profilegithub", "")),
                "Work History": sanitize_for_excel(fields.get("work_history_flat", "")),
                "Education History": sanitize_for_excel(fields.get("education_history_flat", "")),
                "Summary": sanitize_for_excel(fields.get("summary", "")),
            })
        else:
            row["Error Message"] = sanitize_for_excel(result.get("error_message", ""))

        rows.append(row)

    df_results = pd.DataFrame(rows)

    success_count = sum(1 for r in results if r["parse_status"] == "success")
    failed_count = len(results) - success_count
    response_times = [r["response_time_ms"] for r in results if r["parse_status"] == "success" and r["response_time_ms"] > 0]
    avg_time = sum(response_times) / len(response_times) if response_times else 0
    min_time = min(response_times) if response_times else 0
    max_time = max(response_times) if response_times else 0

    summary_data = {
        "Metric": [
            "Total Resumes",
            "Successful Parses",
            "Failed Parses",
            "Success Rate (%)",
            "Avg Response Time (ms)",
            "Min Response Time (ms)",
            "Max Response Time (ms)",
        ],
        "Value": [
            len(results),
            success_count,
            failed_count,
            f"{(success_count / len(results) * 100):.1f}" if results else "0",
            f"{avg_time:.0f}",
            f"{min_time:.0f}",
            f"{max_time:.0f}",
        ],
    }
    df_summary = pd.DataFrame(summary_data)

    try:
        with pd.ExcelWriter(EXCEL_REPORT, engine='openpyxl') as writer:
            df_summary.to_excel(writer, sheet_name="Summary", index=False)
            df_results.to_excel(writer, sheet_name="Results", index=False)
            
            worksheet = writer.sheets['Results']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except Exception:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

        log.info(f"Excel report saved: {EXCEL_REPORT}")
    except Exception as exc:
        log.error(f"Excel write failed: {exc}")
        log.info("Falling back to CSV format...")
        csv_report = OUTPUT_DIR / "bulk_parse_report.csv"
        df_results.to_csv(csv_report, index=False, encoding='utf-8')
        log.info(f"CSV report saved: {csv_report}")

    log.info("")

    log.info("=" * 80)
    log.info("Summary")
    log.info("=" * 80)
    log.info(f"Total resumes: {len(results)}")
    log.info(f"Success: {success_count}")
    log.info(f"Failed: {failed_count}")
    if avg_time > 0:
        log.info(f"Avg response time: {avg_time:.0f} ms (min: {min_time:.0f}, max: {max_time:.0f})")
    log.info(f"Raw responses: {RAW_RESPONSES_DIR}/")
    log.info("=" * 80)


def main():
    log.info("=" * 80)
    log.info("Bulk Resume Parser Logger")
    log.info("=" * 80)
    log.info(f"Resume folder: {RESUME_FOLDER}")
    log.info(f"Output folder: {OUTPUT_DIR}")
    log.info(f"Target: {BASE_URL}")
    log.info("")

    if RAW_RESPONSES_DIR.exists() and list(RAW_RESPONSES_DIR.glob("*.json")):
        log.info("Found existing raw responses.")
        try:
            response = input("Skip parsing and regenerate Excel from saved responses? [y/N]: ").strip().lower()
            if response == 'y':
                results = load_results_from_raw_responses()
                if results:
                    generate_excel_report(results)
                    return
                else:
                    log.warning("Could not load results. Will proceed with fresh parsing.")
        except (EOFError, KeyboardInterrupt):
            log.info("Proceeding with fresh parsing...")
    
    token = get_token(BASE_URL, EMAIL, PASSWORD)
    log.info("")

    resume_files = sorted(RESUME_FOLDER.glob("*.pdf"))
    if not resume_files:
        log.error(f"No PDF files found in {RESUME_FOLDER}")
        return

    log.info(f"Found {len(resume_files)} resume(s)")
    log.info("")

    results = []
    for idx, resume_path in enumerate(resume_files, start=1):
        log.info(f"[{idx}/{len(resume_files)}] Parsing {resume_path.name} ...")
        result = parse_resume(token, resume_path)
        results.append(result)
        time.sleep(0.5)

    generate_excel_report(results)


if __name__ == "__main__":
    main()
