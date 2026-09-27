#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Job Parser Validation Script
-----------------------------
- Processes job descriptions through parser API
- Extracts key fields from parsed results
- Generates Excel report with separate sheet per JD
"""

import os, time, json, requests, logging, re
import pandas as pd
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------- CONFIG ----------------
# JD folder
JD_FOLDER = Path("JD")

# Login credentials
PARSER_EMAIL = os.getenv("PARSER_EMAIL", "")
PARSER_PASSWORD = os.getenv("PARSER_PASSWORD", "")

# Auth token (will be auto-fetched)
PARSER_TOKEN = ""

# Endpoints - Using albatross-ss domain
LOGIN_URL = "https://albatross-dev.recruitcrm.net/v1/login"
PRESIGN_URL = "https://albatross-dev.recruitcrm.net/v1/get-presigned-url"
PARSE_URL = "https://albatross-dev.recruitcrm.net/v1/jobs/parse-job"

# Output
RESULT_EXCEL = "Job_Parser_Results.xlsx"
LOGFILE = "job_parser.log"

# Setup logging and directories
os.makedirs("logs", exist_ok=True)
os.makedirs("job_responses", exist_ok=True)

logging.basicConfig(
    filename=f"logs/{LOGFILE}", 
    level=logging.INFO, 
    format="%(asctime)s - %(message)s"
)

# Fields to extract from job parser response
JOB_FIELDS = [
    'name',                      # Title
    'job_type',                  # Job Type
    'minexperienceinyears',      # Min Exp
    'maxexperienceinyears',      # Max Exp
    'city',                      # City
    'state',                     # State
    'country',                   # Country
    'address',                   # Address
    'annualsalarymin',           # Min Salary
    'annualsalarymax',           # Max Salary
    'job_skill',                 # Skills
    'job_document_id'            # Job Document ID (model_tokens)
]

# ---------------- HELPERS ----------------
def login_and_get_token(email: str, password: str) -> str:
    """Login and fetch fresh authentication token"""
    form_data = {
        'email': email,
        'password': password
    }
    
    try:
        print(f"   Attempting login to {LOGIN_URL}...")
        response = requests.post(LOGIN_URL, data=form_data)
        response.raise_for_status()
        data = response.json()
        
        if "data" in data and "token" in data["data"]:
            token = data["data"]["token"]
            logging.info(f"Successfully fetched token for {email}")
            print(f"   ✓ Successfully logged in as {email}")
            print(f"   ✓ Token received (length: {len(token)} chars)")
            return token
        else:
            logging.error(f"Token not found in response for {email}")
            print(f"   ✗ ERROR: Token not found in response")
            print(f"   Response: {json.dumps(data, indent=2)[:200]}")
            raise Exception(f"Failed to get token for {email}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Login request failed for {email}: {e}")
        print(f"   ✗ NETWORK ERROR: {e}")
        raise Exception(f"Login failed for {email}: {e}")
    except Exception as e:
        logging.error(f"Login failed for {email}: {e}")
        print(f"   ✗ ERROR: {e}")
        raise

def get_presigned_url(file_name: str, token: str):
    """Get presigned URL for file upload"""
    params = {"fileName": file_name, "requestType": "put"}
    headers = {"authorization": f"Bearer {token}"}
    try:
        r = requests.get(PRESIGN_URL, headers=headers, params=params)
        if r.status_code == 401:
            logging.error(f"Token authentication failed (401). Token may be expired or invalid.")
            print(f"     ✗ AUTH ERROR: Token expired or invalid (401)")
            raise Exception(f"Authentication failed - Token expired or invalid")
        r.raise_for_status()
        response_data = r.json()
        logging.info(f"Presigned URL obtained for {file_name}")
        return response_data.get("data", response_data)
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to get presigned URL for {file_name}: {e}")
        print(f"     ✗ REQUEST ERROR: Failed to get presigned URL - {e}")
        raise
    except Exception as e:
        logging.error(f"Failed to get presigned URL for {file_name}: {e}")
        print(f"     ✗ ERROR: {e}")
        raise

def upload_to_s3(put_url: str, file_path: Path):
    """Upload file to S3"""
    # Determine content type based on file extension
    ext = file_path.suffix.lower()
    if ext == '.pdf':
        content_type = "application/pdf"
    elif ext == '.docx':
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif ext == '.doc':
        content_type = "application/msword"
    elif ext == '.txt':
        content_type = "text/plain"
    elif ext == '.png':
        content_type = "image/png"
    else:
        content_type = "application/octet-stream"
    
    try:
        file_size = file_path.stat().st_size
        print(f"     → Uploading {file_path.name} ({file_size:,} bytes, type: {ext[1:].upper()})...")
        with open(file_path, "rb") as f:
            resp = requests.put(put_url, data=f, headers={"Content-Type": content_type})
            resp.raise_for_status()
        logging.info(f"File uploaded: {file_path.name}")
        print(f"     ✓ Upload successful")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to upload {file_path.name} to S3: {e}")
        print(f"     ✗ UPLOAD ERROR: Failed to upload to S3 - {e}")
        raise
    except Exception as e:
        logging.error(f"Failed to upload {file_path.name} to S3: {e}")
        print(f"     ✗ ERROR: {e}")
        raise

def call_job_parser(token: str, file_key: str, file_name: str):
    """Call job parser API"""
    payload = {
        "resumeParserData": {
            "detailfilename": {
                "key": file_key,
                "name": file_name
            }
        },
        "onlyParserData": True
    }
    
    headers = {
        "authorization": f"Bearer {token}", 
        "content-type": "application/json"
    }
    
    try:
        print(f"     → Calling parser API...")
        r = requests.post(PARSE_URL, headers=headers, json=payload)
        
        if r.status_code == 401:
            print(f"     ✗ AUTH ERROR: Token expired or invalid (401)")
            logging.error(f"Token authentication failed (401) for {file_name}")
            raise Exception("Authentication failed - Token expired or invalid")
        
        r.raise_for_status()
        response_data = r.json()
        
        # Log job data for debugging
        if "data" in response_data and "job" in response_data["data"]:
            job = response_data["data"]["job"]
            job_title = job.get('name', 'N/A')
            logging.info(f"Parsed job: {job_title}")
            print(f"     ✓ Parser API successful: {job_title}")
        else:
            print(f"     ⚠ WARNING: No job data in response")
            logging.warning(f"No job data in response for {file_name}")
        
        return response_data
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to parse job {file_name}: {e}")
        print(f"     ✗ PARSER ERROR: Request failed - {e}")
        raise
    except Exception as e:
        logging.error(f"Failed to parse job {file_name}: {e}")
        print(f"     ✗ ERROR: {e}")
        raise

def extract_model_info(job_document_id: str) -> tuple:
    """Extract model number and tokens from job_document_id"""
    if not job_document_id:
        return None, None, "No job_document_id"
    
    try:
        # Parse format: "5_1791" where 5 is model and 1791 is tokens
        parts = str(job_document_id).split('_')
        if len(parts) >= 2:
            model_number = int(parts[0])
            tokens_consumed = int(parts[1])
            return model_number, tokens_consumed
        else:
            return None, None, f"Invalid format: {job_document_id}"
    except (ValueError, IndexError) as e:
        return None, None, f"Parse error: {job_document_id} - {e}"

def process_job_description(file_path: Path, token: str):
    """Process a single job description through the parser"""
    logging.info(f"Processing: {file_path.name}")
    print(f"\n  📄 Processing: {file_path.name}")
    
    try:
        # Process through parser
        start_time = time.time()
        
        # Step 1: Get presigned URL
        print(f"     [1/3] Getting presigned URL...")
        presign = get_presigned_url(file_path.name, token)
        print(f"     ✓ Presigned URL obtained")
        
        # Step 2: Upload to S3
        print(f"     [2/3] Uploading to S3...")
        upload_to_s3(presign["preSignedUrl"], file_path)
        
        # Get the file key
        key = presign["key"]
        print(f"     ✓ File key: {key[:50]}...")
        
        # Step 3: Call parser
        print(f"     [3/3] Parsing job description...")
        parse_result = call_job_parser(token, key, file_path.name)
        
        response_time = round(time.time() - start_time, 2)
        
        # Save raw response for debugging
        response_file = f"job_responses/{file_path.stem}_response.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(parse_result, f, indent=2, ensure_ascii=False)
        logging.info(f"Response saved to {response_file}")
        print(f"     ✓ Response saved to {response_file}")
        
        print(f"  ✅ SUCCESS - Completed in {response_time}s")
        return parse_result, response_time
        
    except Exception as e:
        response_time = round(time.time() - start_time, 2) if 'start_time' in locals() else 0
        logging.exception(f"Failed to process {file_path.name}: {e}")
        print(f"\n  ❌ FAILED after {response_time}s")
        print(f"     Error: {str(e)[:200]}")
        print(f"     Check logs/job_parser.log for full details")
        return None, response_time

def extract_job_fields(parse_result: dict) -> dict:
    """Extract job fields from parser response"""
    if not parse_result or "data" not in parse_result or "job" not in parse_result["data"]:
        return {field: "" for field in JOB_FIELDS}
    
    job_data = parse_result["data"]["job"]
    
    # Extract all fields
    extracted_data = {}
    for field in JOB_FIELDS:
        value = job_data.get(field, "")
        # Convert None to empty string
        if value is None:
            value = ""
        extracted_data[field] = value
    
    return extracted_data

def format_field_name(field: str) -> str:
    """Format field name for display"""
    field_name_mapping = {
        'name': 'Job Title',
        'job_type': 'Job Type',
        'minexperienceinyears': 'Min Experience (Years)',
        'maxexperienceinyears': 'Max Experience (Years)',
        'city': 'City',
        'state': 'State',
        'country': 'Country',
        'address': 'Full Address',
        'annualsalarymin': 'Min Salary (Annual)',
        'annualsalarymax': 'Max Salary (Annual)',
        'job_skill': 'Skills',
        'job_document_id': 'Job Document ID'
    }
    return field_name_mapping.get(field, field)

def main():
    global PARSER_TOKEN
    
    print("="*80)
    print("Job Parser Validation Script")
    print("="*80)
    
    # Fetch fresh token
    print("\n🔐 Authenticating...")
    try:
        PARSER_TOKEN = login_and_get_token(PARSER_EMAIL, PARSER_PASSWORD)
    except Exception as e:
        print(f"✗ Failed to fetch token: {e}")
        return
    
    # Get all JD files from JD folder
    print(f"\n📁 Scanning folder: {JD_FOLDER.absolute()}")
    
    if not JD_FOLDER.exists():
        print(f"❌ ERROR: Folder not found!")
        print(f"   Expected location: {JD_FOLDER.absolute()}")
        print(f"   Please create the folder and add JD files.")
        return
    
    # Get all JD files (PDF, DOC, DOCX, TXT, PNG) with natural sorting
    def natural_sort_key(path):
        """Extract number from filename for natural sorting"""
        match = re.search(r'(\d+)', path.stem)
        return int(match.group(1)) if match else 0
    
    # Get all supported file types
    jd_files = []
    for ext in ['*.pdf', '*.doc', '*.docx', '*.txt', '*.png']:
        jd_files.extend(list(JD_FOLDER.glob(ext)))
    
    jd_files = sorted(jd_files, key=natural_sort_key)
    
    if not jd_files:
        print(f"✗ No JD files found in {JD_FOLDER} (supported formats: PDF, DOC, DOCX, TXT, PNG)")
        return
    
    print(f"✓ Found {len(jd_files)} JD files (PDF/DOC/DOCX/TXT/PNG)")
    print(f"\n📋 Files to process:")
    for idx, jd_file in enumerate(jd_files, 1):
        file_size = jd_file.stat().st_size
        print(f"   {idx:2d}. {jd_file.name} ({file_size:,} bytes)")
    
    # Process all JDs
    print(f"\n{'='*80}")
    print(f"🚀 PROCESSING {len(jd_files)} JOB DESCRIPTIONS")
    print(f"{'='*80}")
    
    all_results = []
    summary_stats = []
    failed_jds = []
    
    for idx, file_path in enumerate(jd_files, 1):
        print(f"\n{'─'*80}")
        print(f"[{idx}/{len(jd_files)}] {file_path.name}")
        print(f"{'─'*80}")
        
        # Process JD
        parse_result, response_time = process_job_description(file_path, PARSER_TOKEN)
        
        # Extract fields
        job_fields = extract_job_fields(parse_result)
        
        # Extract model info
        job_doc_id = job_fields.get('job_document_id', '')
        model_number, tokens_consumed = extract_model_info(job_doc_id)[:2]
        
        # Check if parsing was successful
        is_success = job_fields.get('name', '') != ''
        
        if model_number:
            print(f"  📊 Model: #{model_number} | Tokens: {tokens_consumed:,}")
        
        # Track failures
        if not is_success:
            failed_jds.append({
                'file': file_path.name,
                'reason': 'No job title extracted - Parser may have failed'
            })
            print(f"  ⚠️  WARNING: No job title extracted!")
        
        # Store for individual sheet
        result_data = {
            "JD File": file_path.name,
            "Response Time (s)": response_time,
            "Model Number": model_number if model_number else "Unknown",
            "Tokens Consumed": tokens_consumed if tokens_consumed else "Unknown",
            "Status": "✓ Success" if is_success else "✗ Failed"
        }
        
        # Add all job fields
        for field in JOB_FIELDS:
            result_data[format_field_name(field)] = job_fields.get(field, "")
        
        all_results.append(result_data)
        
        # Store summary stats
        summary_stats.append({
            "JD File": file_path.name,
            "Job Title": job_fields.get('name', '❌ FAILED'),
            "Location": f"{job_fields.get('city', '')}, {job_fields.get('state', '')}, {job_fields.get('country', '')}".strip(', '),
            "Experience Range": f"{job_fields.get('minexperienceinyears', '')} - {job_fields.get('maxexperienceinyears', '')} years" if job_fields.get('minexperienceinyears') or job_fields.get('maxexperienceinyears') else "",
            "Salary Range": f"{job_fields.get('annualsalarymin', '')} - {job_fields.get('annualsalarymax', '')}".strip(' -'),
            "Model Number": model_number if model_number else "Unknown",
            "Tokens": tokens_consumed if tokens_consumed else "Unknown",
            "Response Time (s)": response_time,
            "Status": "✓" if is_success else "✗"
        })
        
        # Small delay between requests
        time.sleep(0.5)
    
    # Verify all files were processed
    print(f"\n{'='*80}")
    print(f"📊 PROCESSING SUMMARY")
    print(f"{'='*80}")
    print(f"   Total files found: {len(jd_files)}")
    print(f"   Results collected: {len(all_results)}")
    print(f"   Summary stats: {len(summary_stats)}")
    
    if len(all_results) != len(jd_files):
        print(f"\n   ⚠️  WARNING: Mismatch detected!")
        print(f"   Expected {len(jd_files)} results but got {len(all_results)}")
        print(f"   Some files may have failed to process")
    
    # Create Excel report
    print(f"\n{'='*80}")
    print(f"💾 GENERATING EXCEL REPORT")
    print(f"{'='*80}")
    
    results_df = pd.DataFrame(all_results)
    summary_df = pd.DataFrame(summary_stats)
    
    print(f"\n   Creating Excel file: {RESULT_EXCEL}")
    print(f"   Will create {len(all_results)} individual JD sheets")
    
    try:
        # Write to Excel with multiple sheets
        with pd.ExcelWriter(RESULT_EXCEL, engine="openpyxl") as writer:
            
            # Sheet 1: Overall Summary
            summary_df.to_excel(writer, sheet_name="Summary", index=False)
            
            # Individual sheet for each JD
            used_sheet_names = set()  # Track used sheet names to avoid duplicates
            sheets_created = 0
            
            for idx, result in enumerate(all_results, 1):
                jd_file = result["JD File"]
                
                # Extract JD number from filename (e.g., "job_description_5.pdf" -> 5)
                jd_match = re.search(r'(\d+)', jd_file)
                jd_number = jd_match.group(1) if jd_match else idx
                
                # Create clean sheet name with actual JD number
                base_sheet_name = f"JD-{jd_number}"
                
                # Ensure sheet name is unique (Excel sheet names must be unique)
                sheet_name = base_sheet_name
                counter = 1
                while sheet_name in used_sheet_names:
                    sheet_name = f"{base_sheet_name}-{counter}"
                    counter += 1
                
                used_sheet_names.add(sheet_name)
                sheets_created += 1
                
                # Prepare data for vertical layout (Field: Value)
                field_value_pairs = []
                for key, value in result.items():
                    if key != "JD File":  # Skip JD File as it's in the sheet name
                        field_value_pairs.append({
                            "Field": key,
                            "Value": str(value)
                        })
                
                # Create dataframe
                jd_df = pd.DataFrame(field_value_pairs)
                
                # Write to sheet
                jd_df.to_excel(writer, sheet_name=sheet_name, index=False)
                
                # Get worksheet for formatting
                ws = writer.sheets[sheet_name]
                
                # Add JD filename as title
                ws.insert_rows(0)
                ws['A1'] = f"Job Description: {jd_file}"
                ws['A1'].font = Font(bold=True, size=12)
                
                # Auto-adjust column widths
                for column in ws.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if cell.value and len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 80)  # Cap at 80 characters
                    ws.column_dimensions[column_letter].width = adjusted_width
            
            print(f"   ✓ Created sheet {sheets_created}/{len(all_results)}: {sheet_name} ({jd_file})")
            
            # Sheet: Model Statistics
            model_stats = summary_df.groupby('Model Number').agg({
                'JD File': 'count',
                'Tokens': lambda x: pd.to_numeric(x, errors='coerce').sum(),
                'Response Time (s)': 'mean'
            }).reset_index()
            
            model_stats.columns = ['Model Number', 'Usage Count', 'Total Tokens', 'Avg Response Time (s)']
            model_stats['Avg Response Time (s)'] = model_stats['Avg Response Time (s)'].round(2)
            model_stats.to_excel(writer, sheet_name="Model Statistics", index=False)
            
            # Format all sheets
            for sheet_name in ["Summary", "Model Statistics"]:
                if sheet_name in writer.sheets:
                    ws = writer.sheets[sheet_name]
                    for column in ws.columns:
                        max_length = 0
                        column_letter = column[0].column_letter
                        for cell in column:
                            try:
                                if cell.value and len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except:
                                pass
                        adjusted_width = min(max_length + 2, 60)
                        ws.column_dimensions[column_letter].width = adjusted_width
        
        print(f"\n   ✓ Summary sheet created")
        print(f"   ✓ {sheets_created} individual JD sheets created (expected {len(all_results)})")
        print(f"   ✓ Model statistics sheet created")
        
        if sheets_created != len(all_results):
            print(f"\n   ⚠️  WARNING: Sheet count mismatch!")
            print(f"   Expected {len(all_results)} sheets but created {sheets_created}")
        print(f"\n✅ Excel report generated: {RESULT_EXCEL}")
        print(f"✅ Raw responses saved in: job_responses/")
        
    except Exception as e:
        print(f"\n❌ ERROR: Failed to generate Excel report")
        print(f"   Error: {e}")
        print(f"   Check logs/job_parser.log for details")
        logging.exception(f"Failed to generate Excel report: {e}")
        return
    
    # Print summary
    print(f"\n" + "="*80)
    print(f"📊 JOB PARSER SUMMARY")
    print(f"="*80)
    
    successful_count = len([r for r in all_results if r['Status'] == '✓ Success'])
    failed_count = len([r for r in all_results if r['Status'] == '✗ Failed'])
    
    print(f"\n🎯 OVERALL STATS:")
    print(f"   Total JDs processed: {len(all_results)}")
    print(f"   ✅ Successful: {successful_count}")
    print(f"   ❌ Failed: {failed_count}")
    
    if failed_jds:
        print(f"\n⚠️  FAILED JOB DESCRIPTIONS:")
        for idx, failed in enumerate(failed_jds, 1):
            print(f"   {idx}. {failed['file']}")
            print(f"      Reason: {failed['reason']}")
    
    # Token stats
    total_tokens = sum(int(s['Tokens']) for s in summary_stats if str(s['Tokens']).isdigit())
    avg_tokens = total_tokens / len(summary_stats) if summary_stats else 0
    avg_time = sum(s['Response Time (s)'] for s in summary_stats) / len(summary_stats) if summary_stats else 0
    
    print(f"\n💰 RESOURCE CONSUMPTION:")
    print(f"   Total tokens consumed: {total_tokens:,}")
    print(f"   Average tokens per JD: {avg_tokens:.0f}")
    print(f"   Average response time: {avg_time:.2f}s")
    
    # Model breakdown
    model_counts = {}
    for stat in summary_stats:
        model = stat['Model Number']
        if model != "Unknown":
            model_counts[model] = model_counts.get(model, 0) + 1
    
    if model_counts:
        print(f"\n🤖 MODEL USAGE:")
        for model, count in sorted(model_counts.items()):
            print(f"   Model #{model}: {count} JDs")
    
    print(f"\n📈 EXCEL SHEETS GENERATED:")
    print(f"   • Summary (all JDs overview)")
    print(f"   • Individual JD sheets (JD-1, JD-2, ... JD-10)")
    print(f"   • Model Statistics")
    
    print(f"\n📍 OUTPUT LOCATION:")
    excel_path = Path(RESULT_EXCEL).absolute()
    print(f"   {excel_path}")
    
    if failed_count > 0:
        print(f"\n⚠️  ATTENTION:")
        print(f"   {failed_count} JD(s) failed to parse properly.")
        print(f"   Check individual sheets and logs/job_parser.log for details.")
    else:
        print(f"\n🎉 ALL JOB DESCRIPTIONS PROCESSED SUCCESSFULLY!")
    
    print(f"\n" + "="*80)

if __name__ == "__main__":
    main()


