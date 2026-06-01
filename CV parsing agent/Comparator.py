#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Resume Parser Accuracy Automation (Recruit CRM)
------------------------------------------------
- Uploads resumes to presigned S3 URLs
- Calls Sovren Parser (flag OFF) and In-House LLM Parser (flag ON)
- Extracts parsed data (backend already filters fields)
- Compares outputs field-by-field
- Calculates accuracy metrics and writes Excel dashboard
"""

import os, time, json, base64, requests, logging
import datetime
import re
import pandas as pd
from difflib import SequenceMatcher
from pathlib import Path
from openpyxl import Workbook

from config import (
    COMPARATOR_BASE_URL as BASE_URL,
    SOVREN_EMAIL, SOVREN_PASSWORD,
    INHOUSE_EMAIL, INHOUSE_PASSWORD,
    OPENAI_API_KEY,
)

RESUME_FOLDER = Path(__file__).parent / "100 resumes"

SOVREN_TOKEN = ""
INHOUSE_TOKEN = ""

USE_AI_COMPARISON = False
AI_MODEL = "gpt-4o-mini"
AI_COMPARISON_FIELDS = ["resumeText"]

PRESIGN_URL   = f"{BASE_URL}/v1/get-presigned-url"
PARSE_URL     = f"{BASE_URL}/v1/candidates/parse-resume?actionsteps=1"
WORK_HISTORY_URL      = f"{BASE_URL}/v1/candidates/candidate-work/{{candidate_id}}"
EDUCATION_HISTORY_URL = f"{BASE_URL}/v1/candidates/candidate-education/{{candidate_id}}"

RESULT_EXCEL = "Parser_Accuracy_Report.xlsx"
LOGFILE = "parser_accuracy.log"
CACHE_FOLDER = "sovren_cache"
os.makedirs("logs", exist_ok=True)
os.makedirs(CACHE_FOLDER, exist_ok=True)

logging.basicConfig(filename=f"logs/{LOGFILE}", level=logging.INFO, format="%(asctime)s - %(message)s")

def login_and_get_token(email: str, password: str) -> str:
    """Login and fetch fresh authentication token"""
    login_url = f"{BASE_URL}/v1/login"

    form_data = {
        'email': email,
        'password': password
    }
    
    try:
        response = requests.post(login_url, data=form_data)
        response.raise_for_status()
        data = response.json()
        
        if "data" in data and "token" in data["data"]:
            token = data["data"]["token"]
            logging.info(f"Successfully fetched token for {email}")
            return token
        else:
            logging.error(f"Token not found in response for {email}")
            raise Exception(f"Failed to get token for {email}")
    except Exception as e:
        logging.error(f"Login failed for {email}: {e}")
        raise Exception(f"Login failed for {email}: {e}")

def ai_compare_fields(field_name: str, sovren_value: str, inhouse_value: str) -> tuple:
    """
    Use AI to intelligently compare two field values
    Returns: (classification, score, explanation)
    """
    if not USE_AI_COMPARISON or field_name not in AI_COMPARISON_FIELDS:
        return None, None, None

    api_key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logging.warning("OpenAI API key not set. Falling back to traditional comparison.")
        return None, None, None
    
    prompt = f"""You are an expert at comparing resume parsing outputs. Compare the following two parsed values for the field "{field_name}":

SOVREN OUTPUT:
{sovren_value[:2000]}  

INHOUSE OUTPUT:
{inhouse_value[:2000]}

Analyze which parser extracted more complete and accurate information. Consider:
1. Completeness: Does one have more relevant details?
2. Accuracy: Are the facts correct and properly extracted?
3. Structure: Is the information well-organized?

Respond in JSON format:
{{
    "verdict": "inhouse_better" | "sovren_better" | "equal" | "inhouse_extra" | "inhouse_missing",
    "score": <number between -1 and 1, where -1 means Sovren much better, 0 means equal, 1 means InHouse much better>,
    "explanation": "<brief explanation of your decision>"
}}"""

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "You are an expert at comparing resume parsing outputs. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 500
            },
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        content = result["choices"][0]["message"]["content"]

        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            ai_result = json.loads(json_match.group())
            verdict = ai_result.get("verdict", "equal")
            score = ai_result.get("score", 0)
            explanation = ai_result.get("explanation", "")

            if verdict == "inhouse_better":
                classification = "InHouse Better (+1)"
                final_score = 1
            elif verdict == "sovren_better":
                classification = "Sovren Better (-1)"
                final_score = -1
            elif verdict == "inhouse_extra":
                classification = "InHouse Has Extra Data (+1)"
                final_score = 1
            elif verdict == "inhouse_missing":
                classification = "InHouse Missing Data (-1)"
                final_score = -1
            else:
                classification = "Equal (AI)"
                final_score = 0
            
            logging.info(f"AI comparison for {field_name}: {classification} - {explanation}")
            return classification, final_score, explanation
            
    except Exception as e:
        logging.error(f"AI comparison failed for {field_name}: {e}")
        return None, None, None
    
    return None, None, None

def get_presigned_url(file_name: str, token: str):
    params = {"fileName": file_name, "requestType": "put"}
    headers = {"authorization": f"Bearer {token}"}
    r = requests.get(PRESIGN_URL, headers=headers, params=params)
    if r.status_code == 401:
        logging.error(f"Token authentication failed (401). Token may be expired or invalid.")
        raise Exception(f"Authentication failed - Token expired or invalid")
    r.raise_for_status()
    response_data = r.json()
    return response_data.get("data", response_data)

def upload_to_s3(put_url: str, file_path: Path):
    with open(file_path, "rb") as f:
        resp = requests.put(put_url, data=f, headers={"Content-Type": "application/pdf"})
        resp.raise_for_status()
    return True

def call_parser(token: str, file_key: str, file_name: str, file_size: int):
    payload = {
        "resumeParserData": {
            "resumesParsed": 0,
            "resumesFailed": 0,
            "resumesTotal": 1,
            "filesInfo": {
                "key":   file_key,
                "name":  file_name,
                "type":  "application/pdf",
                "size":  file_size,
                "index": 0,
            },
        },
        "actionid": 0,
    }
    headers = {
        "Authorization":  f"Bearer {token}",
        "Content-Type":   "application/json",
        "Accept":         "application/json, text/plain, */*",
        "Referer":        "https://app.recruitcrm.io/",
    }
    r = requests.post(PARSE_URL, headers=headers, json=payload)
    r.raise_for_status()
    response_data = r.json()
    if "data" in response_data and "candidate" in response_data["data"]:
        candidate = response_data["data"]["candidate"]
        logging.info(
            f"Parsed candidate id={candidate.get('id')} "
            f"name={candidate.get('firstname')} {candidate.get('lastname')} "
            f"email={candidate.get('emailid')}"
        )
    return response_data

def fetch_work_history(token: str, candidate_id: int):
    """Fetch detailed work history for a candidate"""
    url = WORK_HISTORY_URL.format(candidate_id=candidate_id)
    headers = {"authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        work_data = r.json()
        logging.info(f"Work history fetched for candidate {candidate_id}")
        return work_data.get("data", [])
    except Exception as e:
        logging.error(f"Failed to fetch work history for candidate {candidate_id}: {e}")
        return []

def fetch_education_history(token: str, candidate_id: int):
    """Fetch detailed education history for a candidate"""
    url = EDUCATION_HISTORY_URL.format(candidate_id=candidate_id)
    headers = {"authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        edu_data = r.json()
        logging.info(f"Education history fetched for candidate {candidate_id}")
        return edu_data.get("data", [])
    except Exception as e:
        logging.error(f"Failed to fetch education history for candidate {candidate_id}: {e}")
        return []

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, str(a).lower().strip(), str(b).lower().strip()).ratio()

def classify_field(sov: str, inh: str, field_name: str = ""):
    """
    New scoring system with AI integration:
    - Match: 0 (equal performance)
    - Extra Data: +1 (InHouse has more data)
    - Mismatch: -1 (InHouse is wrong)
    - Partial: 0 (needs manual review, neutral score)
    
    Special rule: If Sovren is empty but InHouse has value (except resumeText), give +1
    For complex fields (resumeText, workHistory, etc.), uses AI to compare
    """
    sov_empty = not sov or sov.lower() in ["none", "null", ""]
    inh_empty = not inh or inh.lower() in ["none", "null", ""]

    if sov_empty and not inh_empty and field_name != "resumeText":
        return "InHouse Has Data, Sovren Empty (+1)", 1
    
    if sov_empty and inh_empty:
        return "Both Empty (0)", 0

    if inh_empty and not sov_empty:
        return "InHouse Missing Data (-1)", -1

    if field_name and USE_AI_COMPARISON and field_name in AI_COMPARISON_FIELDS:
        ai_result = ai_compare_fields(field_name, sov, inh)
        if ai_result is not None:
            classification, score, explanation = ai_result
            if explanation:
                classification = f"{classification} [AI: {explanation}]"
            return classification, score
    
    sim = similarity(sov, inh)
    if sim == 1.0:
        return "Match", 0
    elif sim >= 0.85:
        return "Partial Match (Manual Review)", 0
    elif len(inh) > len(sov) and sim > 0.6:
        return "Extra Data (+1)", 1
    else:
        return "Mismatch (-1)", -1

def process_resume(file_path: Path):
    logging.info(f"Processing: {file_path.name}")
    results = {}
    response_times = {}
    os.makedirs("raw_responses", exist_ok=True)
    
    for label, token in [("Sovren", SOVREN_TOKEN), ("InHouse", INHOUSE_TOKEN)]:
        start_time = time.time()

        presign = get_presigned_url(file_path.name, token)
        upload_to_s3(presign["preSignedUrl"], file_path)
        key = presign["key"]
        parse_result = call_parser(token, key, file_path.name, file_path.stat().st_size)

        if "data" in parse_result and "candidate" in parse_result["data"]:
            candidate_id = parse_result["data"]["candidate"].get("id")
            if candidate_id:
                work_history = fetch_work_history(token, candidate_id)
                if work_history:
                    parse_result["data"]["work_history"] = work_history

                edu_history = fetch_education_history(token, candidate_id)
                if edu_history:
                    parse_result["data"]["education_history"] = edu_history
        
        response_times[label] = round(time.time() - start_time, 2)

        results[label] = parse_result

        response_file = f"raw_responses/{file_path.stem}_{label}.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(parse_result, f, indent=2, ensure_ascii=False)
        logging.info(f"{label} parsed successfully for {file_path.name} - Response saved to {response_file}")
        
        time.sleep(1)
    
    results["response_times"] = response_times
    return results

def extract_work_history(data: dict) -> str:
    """Extract work history from parsed data including detailed work history API"""
    work_exp = []
    
    if "work_history" in data and data["work_history"]:
        for work in data["work_history"]:
            company = work.get("work_company_name", "")
            title = work.get("title", "")
            start_ts = work.get("work_start_date", "")
            end_ts = work.get("work_end_date", "")

            start = ""
            end = "Present"
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
            
            if company or title:
                work_exp.append(f"{title} at {company} ({start} - {end})")
    else:
        candidate = data.get("candidate", {})
        if "workexpyr" in candidate:
            work_exp.append(f"Total Experience: {candidate.get('workexpyr', 0)} years")
        if "lastorganisation" in candidate and candidate.get("lastorganisation"):
            work_exp.append(f"Last Org: {candidate['lastorganisation']}")
        if "position" in candidate and candidate.get("position"):
            work_exp.append(f"Position: {candidate['position']}")
    
    return " | ".join(work_exp) if work_exp else ""

def extract_education_history(data: dict) -> str:
    """Extract education history from parsed data including detailed education API"""
    edu = []
    
    if "education_history" in data and data["education_history"]:
        for education in data["education_history"]:
            degree = education.get("educational_qualification", "")
            institute = education.get("institute_name", "")
            specialization = education.get("educational_specialization", "")
            start_ts = education.get("education_start_date", "")
            end_ts = education.get("education_end_date", "")
            
            start_year = "NaN"
            end_year = "NaN"

            try:
                if start_ts and isinstance(start_ts, (int, float)) and start_ts > 0:
                    start_year = datetime.datetime.fromtimestamp(start_ts).strftime("%Y")
            except (ValueError, OSError, OverflowError):
                start_year = "NaN"

            try:
                if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                    end_year = datetime.datetime.fromtimestamp(end_ts).strftime("%Y")
            except (ValueError, OSError, OverflowError):
                end_year = "NaN"
            
            if degree or institute:
                edu_str = f"{degree}"
                if specialization:
                    edu_str += f" in {specialization}"
                if institute:
                    edu_str += f" from {institute}"
                edu_str += f" ({start_year}-{end_year})"
                edu.append(edu_str)
    else:
        candidate = data.get("candidate", {})
        if "qualification" in candidate and candidate.get("qualification"):
            edu.append(f"Qualification: {candidate['qualification']}")
        if "specialization" in candidate and candidate.get("specialization"):
            edu.append(f"Specialization: {candidate['specialization']}")
    
    return " | ".join(edu) if edu else ""

def extract_social_media(candidate_data: dict) -> str:
    """Extract social media profiles"""
    social = []
    for platform in ["profilelinkedin", "profilegithub", "profilefacebook", "profiletwitter", "profilexing"]:
        if candidate_data.get(platform):
            social.append(f"{platform.replace('profile', '')}: {candidate_data[platform]}")
    return " | ".join(social) if social else ""

def validate_critical_field(sovren_val: str, inhouse_val: str, field_name: str) -> tuple:
    """
    Validate critical fields (name, email, phone)
    Returns: (is_valid, remark)
    Only fails if BOTH have values and they don't match
    """
    sov_empty = not sovren_val or sovren_val.lower() in ["none", "null", ""]
    inh_empty = not inhouse_val or inhouse_val.lower() in ["none", "null", ""]
    
    if sov_empty and not inh_empty:
        return True, ""
    
    if sov_empty and inh_empty:
        return True, ""
    
    if inh_empty and not sov_empty:
        return True, ""
    
    sim = similarity(sovren_val, inhouse_val)
    if sim >= 0.85:
        return True, ""
    else:
        return False, f"{field_name} mismatch"

def compare_skills(sovren_skills: str, inhouse_skills: str) -> tuple:
    """
    Compare skills with special logic:
    - InHouse must have >= skills than Sovren
    - If equal count, sort and compare
    Returns: (is_valid, match_type, score, remark)
    """
    sov_empty = not sovren_skills or sovren_skills.lower() in ["none", "null", ""]
    inh_empty = not inhouse_skills or inhouse_skills.lower() in ["none", "null", ""]
    
    if sov_empty and inh_empty:
        return True, "Both Empty (0)", 0, ""
    
    if sov_empty and not inh_empty:
        return True, "InHouse Has Data, Sovren Empty (+1)", 1, ""
    
    if inh_empty and not sov_empty:
        return True, "InHouse Missing Data (-1)", -1, ""
    
    sov_list = [s.strip() for s in re.split(r'[,|;]+', sovren_skills) if s.strip()]
    inh_list = [s.strip() for s in re.split(r'[,|;]+', inhouse_skills) if s.strip()]
    
    sov_count = len(sov_list)
    inh_count = len(inh_list)
    
    if inh_count < sov_count:
        return False, "Skills Mismatch - Fewer Skills (-1)", -1, f"skills (InHouse: {inh_count}, Sovren: {sov_count})"
    
    if inh_count == sov_count:
        sov_sorted = sorted([s.lower() for s in sov_list])
        inh_sorted = sorted([s.lower() for s in inh_list])
        
        if sov_sorted == inh_sorted:
            return True, "Match", 0, ""
        else:
            return False, "Skills Mismatch - Content Different (-1)", -1, "skills (content mismatch)"
    
    return True, "Extra Skills (+1)", 1, ""

def compare_language_skills(sovren_lang: str, inhouse_lang: str) -> tuple:
    """
    Compare language skills with count-based logic:
    - InHouse has MORE languages → +1 (Extra data, not critical)
    - Sovren has MORE languages → Critical failure
    - Equal count → Sort and compare
    Returns: (is_valid, match_type, score, remark)
    """
    sov_empty = not sovren_lang or sovren_lang.lower() in ["none", "null", ""]
    inh_empty = not inhouse_lang or inhouse_lang.lower() in ["none", "null", ""]
    
    if sov_empty and inh_empty:
        return True, "Both Empty (0)", 0, ""
    
    if sov_empty and not inh_empty:
        return True, "InHouse Has Data, Sovren Empty (+1)", 1, ""
    
    if inh_empty and not sov_empty:
        return True, "InHouse Missing Data (-1)", -1, ""
    
    sov_list = sorted([s.strip().lower() for s in re.split(r'[,|;]+', sovren_lang) if s.strip()])
    inh_list = sorted([s.strip().lower() for s in re.split(r'[,|;]+', inhouse_lang) if s.strip()])
    
    sov_count = len(sov_list)
    inh_count = len(inh_list)
    
    if inh_count > sov_count:
        return True, "Extra Language Skills (+1)", 1, ""
    
    if sov_count > inh_count:
        return False, "Language Skills Mismatch - Fewer Languages (-1)", -1, f"languageSkills (InHouse: {inh_count}, Sovren: {sov_count})"
    
    if sov_list == inh_list:
        return True, "Match", 0, ""
    else:
        return False, "Language Skills Mismatch - Content Different (-1)", -1, "languageSkills (content mismatch)"

def compare_fields(sovren_data: dict, inhouse_data: dict):
    candidate_s = sovren_data["data"]["candidate"]
    candidate_i = inhouse_data["data"]["candidate"]

    critical_failures = []
    field_mismatches = []

    field_map = [
        ("email", "emailid"),
        ("phone", "contactnumber"),
        ("city", "city"),
        ("state", "state"),
        ("country", "country"),
        ("fullAddress", "address"),
        ("specialization", "specialization"),
        ("workExperience", "workexpyr"),
        ("currentOrganization", "lastorganisation"),
        ("title", "position"),
        ("skills", "skill"),
        ("languageSkills", "languageskills"),
        ("educationalQualification", "qualification"),
        ("resumeText", "resumetext"),
        ("gender", "gender"),
    ]
    
    rows = []

    s_fullname = f"{candidate_s.get('firstname', '')} {candidate_s.get('lastname', '')}".strip()
    i_fullname = f"{candidate_i.get('firstname', '')} {candidate_i.get('lastname', '')}".strip()
    
    sovren_has_placeholder = any(placeholder in s_fullname.lower() for placeholder in ["not available", "n/a", "na", "not found", "unknown"])

    if sovren_has_placeholder and i_fullname and i_fullname.lower() not in ["not available", "n/a", "na", "not found", "unknown"]:
        match_type = "InHouse Has Data, Sovren Empty (+1)"
        score = 1
        is_valid = True
        remark = ""
    else:
        is_valid, remark = validate_critical_field(s_fullname, i_fullname, "fullName")
        if not is_valid:
            critical_failures.append(remark)
        match_type, score = classify_field(s_fullname, i_fullname, "fullName")
    
    rows.append({
        "Field": "fullName",
        "Sovren Output": s_fullname,
        "In-House Output": i_fullname,
        "Match Type": match_type,
        "Score": score,
        "Remarks": remark if not is_valid else ""
    })
    
    s_summary = str(candidate_s.get("summary", "")).strip()
    i_summary = str(candidate_i.get("summary", "")).strip()
    summary_remark = ""
    if not i_summary or i_summary.lower() == "none":
        match_type = "Missing Summary (-1)"
        score = -1
        summary_remark = "Summary is empty/null in InHouse"
        field_mismatches.append("summary (empty/null)")
    else:
        match_type = "Summary Present (0)"
        score = 0
    rows.append({
        "Field": "summary",
        "Sovren Output": s_summary,
        "In-House Output": i_summary,
        "Match Type": match_type,
        "Score": score,
        "Remarks": summary_remark
    })
    
    for display_name, field_key in field_map:
        s_val = str(candidate_s.get(field_key, "")).strip()
        i_val = str(candidate_i.get(field_key, "")).strip()
        remark = ""

        if display_name == "email":
            is_valid, remark = validate_critical_field(s_val, i_val, "email")
            if not is_valid:
                critical_failures.append(remark)
            match_type, score = classify_field(s_val, i_val, display_name)

        elif display_name == "phone":
            is_valid, remark = validate_critical_field(s_val, i_val, "phone")
            if not is_valid:
                critical_failures.append(remark)
            match_type, score = classify_field(s_val, i_val, display_name)

        elif display_name == "skills":
            is_valid, match_type, score, remark = compare_skills(s_val, i_val)
            if not is_valid:
                field_mismatches.append(remark)
                remark = f"Skills mismatch (see details)"

        elif display_name == "languageSkills":
            is_valid, match_type, score, remark = compare_language_skills(s_val, i_val)
            if not is_valid:
                field_mismatches.append(remark)
                remark = f"Language skills mismatch (see details)"

        elif display_name == "city":
            is_valid, remark = validate_critical_field(s_val, i_val, "city")
            if not is_valid:
                critical_failures.append(remark)
            match_type, score = classify_field(s_val, i_val, display_name)

        elif display_name in ["state", "country"]:
            match_type, score = classify_field(s_val, i_val, display_name)
            s_empty = not s_val or s_val.lower() in ["none", "null", ""]
            i_empty = not i_val or i_val.lower() in ["none", "null", ""]
            if not s_empty and not i_empty:
                sim = similarity(s_val, i_val)
                if sim < 0.85:
                    field_mismatches.append(f"{display_name} mismatch")
                    remark = f"{display_name} mismatch"

        else:
            match_type, score = classify_field(s_val, i_val, display_name)
        
        rows.append({
            "Field": display_name,
            "Sovren Output": s_val,
            "In-House Output": i_val,
            "Match Type": match_type,
            "Score": score,
            "Remarks": remark
        })
    
    s_work = extract_work_history(sovren_data["data"])
    i_work = extract_work_history(inhouse_data["data"])
    match_type, score = classify_field(s_work, i_work, "workHistory")
    rows.append({
        "Field": "workHistory",
        "Sovren Output": s_work,
        "In-House Output": i_work,
        "Match Type": match_type,
        "Score": score,
        "Remarks": ""
    })

    s_edu = extract_education_history(sovren_data["data"])
    i_edu = extract_education_history(inhouse_data["data"])
    match_type, score = classify_field(s_edu, i_edu, "educationHistory")
    rows.append({
        "Field": "educationHistory",
        "Sovren Output": s_edu,
        "In-House Output": i_edu,
        "Match Type": match_type,
        "Score": score,
        "Remarks": ""
    })

    s_social = extract_social_media(candidate_s)
    i_social = extract_social_media(candidate_i)
    match_type, score = classify_field(s_social, i_social, "socialMedia")
    rows.append({
        "Field": "socialMedia",
        "Sovren Output": s_social,
        "In-House Output": i_social,
        "Match Type": match_type,
        "Score": score,
        "Remarks": ""
    })
    
    df = pd.DataFrame(rows)

    total_score = df["Score"].sum()

    if critical_failures:
        verdict = "Sovren Wins"
        verdict_reason = f"Critical field(s) failed: {', '.join(critical_failures)}"
    else:
        if total_score > 0:
            verdict = "InHouse Wins"
        elif total_score < 0:
            verdict = "Sovren Wins"
        else:
            verdict = "Equal"
        verdict_reason = f"Based on score: {total_score}"
    
    return df, total_score, verdict, critical_failures, field_mismatches, verdict_reason

def main():
    global SOVREN_TOKEN, INHOUSE_TOKEN

    print("Fetching fresh authentication tokens...")
    try:
        SOVREN_TOKEN = login_and_get_token(SOVREN_EMAIL, SOVREN_PASSWORD)
        print("Sovren token fetched successfully")
    except Exception as e:
        print(f"Failed to fetch Sovren token: {e}")
        return
    
    try:
        INHOUSE_TOKEN = login_and_get_token(INHOUSE_EMAIL, INHOUSE_PASSWORD)
        print("InHouse token fetched successfully")
    except Exception as e:
        print(f"Failed to fetch InHouse token: {e}")
        return
    
    print("\nStarting resume comparison...\n")

    if USE_AI_COMPARISON and OPENAI_API_KEY:
        print(f"AI Comparison: ENABLED (using {AI_MODEL})")
        print(f"AI Fields: {', '.join(AI_COMPARISON_FIELDS)}\n")
    else:
        print("AI Comparison: DISABLED (using traditional string matching)\n")
    
    all_resumes = []
    supported_extensions = ['pdf', 'doc', 'docx', 'rtf']
    for ext in supported_extensions:
        all_resumes.extend(RESUME_FOLDER.glob(f"*.{ext}"))
        all_resumes.extend(RESUME_FOLDER.glob(f"*.{ext.upper()}"))
    
    seen = set()
    unique_resumes = []
    for resume in all_resumes:
        resume_lower = str(resume).lower()
        if resume_lower not in seen:
            seen.add(resume_lower)
            unique_resumes.append(resume)
    all_resumes = unique_resumes
    
    print(f"Found {len(all_resumes)} resume(s) to process")
    print(f"Supported formats: {', '.join(supported_extensions)}\n")
    
    summary = []
    writer = pd.ExcelWriter(RESULT_EXCEL, engine="openpyxl")

    resume_counter = 1

    for resume in all_resumes:
        try:
            data = process_resume(resume)
            df, total_score, verdict, critical_failures, field_mismatches, verdict_reason = compare_fields(data["Sovren"], data["InHouse"])

            sheet_name = str(resume_counter)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            worksheet = writer.sheets[sheet_name]

            last_row = len(df) + 2
            worksheet[f'A{last_row}'] = 'TOTAL SCORE'
            worksheet[f'A{last_row}'].font = worksheet[f'A{last_row}'].font.copy(bold=True)
            worksheet[f'E{last_row}'] = f'=SUM(E2:E{last_row-1})'
            worksheet[f'E{last_row}'].font = worksheet[f'E{last_row}'].font.copy(bold=True)

            verdict_row = last_row + 1
            worksheet[f'A{verdict_row}'] = 'VERDICT'
            worksheet[f'A{verdict_row}'].font = worksheet[f'A{verdict_row}'].font.copy(bold=True)
            worksheet[f'E{verdict_row}'] = verdict
            worksheet[f'E{verdict_row}'].font = worksheet[f'E{verdict_row}'].font.copy(bold=True)

            reason_row = verdict_row + 1
            worksheet[f'A{reason_row}'] = 'REASON'
            worksheet[f'A{reason_row}'].font = worksheet[f'A{reason_row}'].font.copy(bold=True)
            worksheet[f'B{reason_row}'] = verdict_reason

            sovren_time = data["response_times"].get("Sovren", 0)
            inhouse_time = data["response_times"].get("InHouse", 0)

            critical_failures_str = ", ".join(critical_failures) if critical_failures else "None"
            field_mismatches_str = ", ".join(field_mismatches) if field_mismatches else "None"
            
            summary.append({
                "Sheet #": resume_counter,
                "Resume": resume.name,
                "Sovren Response Time (s)": sovren_time,
                "InHouse Response Time (s)": inhouse_time,
                "Total Score": total_score,
                "Verdict": verdict,
                "Critical Failures": critical_failures_str,
                "Field Mismatches": field_mismatches_str
            })

            resume_counter += 1
            
        except Exception as e:
            logging.exception(f"Failed: {resume.name} - {e}")
            summary.append({
                "Sheet #": "N/A",
                "Resume": resume.name,
                "Sovren Response Time (s)": "Error",
                "InHouse Response Time (s)": "Error",
                "Total Score": "Error",
                "Verdict": "Failed",
                "Critical Failures": "Error",
                "Field Mismatches": "Error"
            })

    summary_df = pd.DataFrame(summary)
    summary_df.to_excel(writer, sheet_name="Summary", index=False)
    writer.close()
    print(f"\nAccuracy Report generated: {RESULT_EXCEL}")
    print(f"\nSummary:")
    print(summary_df.to_string(index=False))

if __name__ == "__main__":
    main()
