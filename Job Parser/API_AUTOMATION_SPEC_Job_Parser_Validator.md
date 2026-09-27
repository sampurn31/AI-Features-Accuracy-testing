# Job Parser Validator (English) — API Automation Specification

**Purpose:** Use this document when implementing API automation tests for the Job Parser Validator. It describes the APIs, flow, schemas, and test scenarios derived from the reference implementation.

---

## 1. Overview

The Job Parser Validator processes job descriptions (JDs) through a multi-step API flow:

1. **Login** → Obtain Bearer token
2. **Presigned URL** → Get S3 upload URL for the JD file
3. **Upload** → PUT file to S3
4. **Parse** → Call parse-job API with the file key

All APIs are under the **Albatross** base URL. Base domains seen in the reference:
- `https://albatross-dev.recruitcrm.net` (dev)
- `https://albatross-ss.recruitcrm.net` (possible staging/SS)

---

## 2. API Endpoints

### Base URL (configurable)

```
BASE_URL = https://albatross-dev.recruitcrm.net
```

### 2.1 Login

| Property | Value |
|----------|-------|
| **Method** | `POST` |
| **URL** | `{BASE_URL}/v1/login` |
| **Content-Type** | `application/x-www-form-urlencoded` (form data) |

**Request body (form-data):**

| Key | Type | Required |
|-----|------|----------|
| `email` | string | Yes |
| `password` | string | Yes |

**Success response (200):**

```json
{
  "data": {
    "token": "<JWT_BEARER_TOKEN>"
  }
}
```

**Assertions:**
- Status code: `200`
- `data.token` exists and is non-empty
- Token is a valid JWT string

---

### 2.2 Get Presigned URL

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `{BASE_URL}/v1/get-presigned-url` |
| **Auth** | `Authorization: Bearer {token}` |
| **Query params** | `fileName`, `requestType` |

**Query parameters:**

| Param | Type | Example |
|-------|------|---------|
| `fileName` | string | `"job_description.pdf"` |
| `requestType` | string | `"put"` |

**Success response (200):**

```json
{
  "data": {
    "preSignedUrl": "https://...",
    "key": "base64-encoded-s3-key"
  }
}
```

**Assertions:**
- Status code: `200`
- `data.preSignedUrl` exists and is a valid URL
- `data.key` exists and is non-empty

---

### 2.3 Upload to S3

| Property | Value |
|----------|-------|
| **Method** | `PUT` |
| **URL** | From `preSignedUrl` in step 2.2 |
| **Content-Type** | Per file type (see below) |
| **Body** | Raw file bytes |

**Content-Type by extension:**

| Extension | Content-Type |
|-----------|-------------|
| `.pdf` | `application/pdf` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.doc` | `application/msword` |
| `.txt` | `text/plain` |
| `.png` | `image/png` |
| Other | `application/octet-stream` |

**Success:**
- Status code: `200`
- No body validation required; empty or minimal response is OK

---

### 2.4 Parse Job

| Property | Value |
|----------|-------|
| **Method** | `POST` |
| **URL** | `{BASE_URL}/v1/jobs/parse-job` |
| **Auth** | `Authorization: Bearer {token}` |
| **Content-Type** | `application/json` |

**Request body:**

```json
{
  "resumeParserData": {
    "detailfilename": {
      "key": "<key_from_presign_step>",
      "name": "<original_file_name>"
    }
  },
  "onlyParserData": true
}
```

**Success response (200):**

```json
{
  "silent_progress": true,
  "message": "Job Parser Successful ",
  "message_type": "is-success",
  "data": {
    "job": {
      "name": "Frontend Vue.js Developer",
      "job_type": "fulltime",
      "minexperienceinyears": 3,
      "maxexperienceinyears": 6,
      "city": "Hyderabad",
      "state": "Telangana",
      "country": "India",
      "address": "HITEC City, Hyderabad, Telangana, India",
      "job_skill": "Vue.js, JavaScript, HTML, CSS, ...",
      "annualsalarymin": 1500000,
      "annualsalarymax": 2800000,
      "job_document_id": "5_1791"
    }
  }
}
```

**Field mappings (Job Parser Validator):**

| API field | Display / validation purpose |
|-----------|------------------------------|
| `name` | Job Title |
| `job_type` | Job Type |
| `minexperienceinyears` | Min Experience (Years) |
| `maxexperienceinyears` | Max Experience (Years) |
| `city` | City |
| `state` | State |
| `country` | Country |
| `address` | Full Address |
| `annualsalarymin` | Min Salary (Annual) |
| `annualsalarymax` | Max Salary (Annual) |
| `job_skill` | Skills |
| `job_document_id` | Model + token info (format: `{model}_{tokens}`) |

**Assertions:**
- Status code: `200`
- `data.job` exists
- `data.job.name` is non-empty (indicates parsing success)

---

## 3. Error Cases to Test

| Scenario | Expected behavior |
|----------|-------------------|
| Invalid login (wrong email/password) | Non-200, no token |
| Expired/missing token on presign | `401` |
| Expired/missing token on parse | `401` |
| Invalid/unknown `key` in parse payload | Non-200 or empty job data |
| Wrong `fileName` in parse vs upload | May fail or return unexpected results |
| Empty/corrupt file upload | Parse may fail or return no job data |
| Unsupported file type | May fail at presign or parse |

---

## 4. Test Scenarios for Automation

### 4.1 Smoke / Happy path

1. Login with valid credentials → get token.
2. Get presigned URL for a known valid JD file (e.g. `.pdf`).
3. Upload the file to S3.
4. Call parse-job with `key` and original filename.
5. Assert:
   - 200 on parse
   - `data.job.name` is non-empty
   - All expected fields present (name, job_type, city, etc.)

### 4.2 Auth tests

- Login with invalid credentials → expect failure, no token.
- Call presign/parse without token → expect 401.
- Call presign/parse with expired token → expect 401.

### 4.3 End-to-end by format

- Run full flow for each supported format: `.pdf`, `.doc`, `.docx`, `.txt`, `.png`.
- Assert job is parsed successfully for each.

### 4.4 Field validation

- For a known JD (with golden data), assert parsed values for:
  - Job title (`name`)
  - Location (city, state, country)
  - Experience range (min/max years)
  - Salary range (min/max)
  - Skills (`job_skill`)
  - Job type (`job_type`)

### 4.5 `job_document_id` format

- Parse `job_document_id` as `{model}_{tokens}`.
- Assert model and tokens are numeric (e.g. `"5_1791"` → model 5, tokens 1791).

### 4.6 Negative cases

- Invalid `key` in parse payload.
- File not uploaded before parse.
- Empty or corrupt file.

---

## 5. Environment / Config

| Variable | Purpose |
|----------|---------|
| `BASE_URL` | API base (e.g. albatross-dev or albatross-ss) |
| `PARSER_EMAIL` | Login email |
| `PARSER_PASSWORD` | Login password |
| `JD_FOLDER` / `TEST_JD_PATH` | Path to test JD files |

Use env vars or a config file (e.g. `.env`); do not commit credentials.

---

## 6. Suggested Tech Stack

- **Python:** `pytest` + `requests`, optional `pytest-html` for reports.
- **JS/TS:** `jest` or `vitest` + `axios` / `fetch`.
- **API-only:** Postman/Newman or Insomnia with exported collections.

Recommended structure:

```
tests/
  api/
    test_login.py
    test_presign.py
    test_parse_job.py
    test_e2e_flow.py
fixtures/
  sample_jd.pdf
  sample_jd.docx
config/
  env.example
```

---

## 7. Flow Sequence Diagram (for reference)

```
┌──────┐     POST /v1/login          ┌─────────┐
│      │ ──────────────────────────► │  Auth   │
│ Test │   email, password           │  API    │
│      │ ◄────────────────────────── │         │
│      │   { token }                 └─────────┘
└──┬───┘
   │
   │ GET /v1/get-presigned-url
   │ ?fileName=xxx.pdf&requestType=put
   │ Bearer token
   ▼
┌─────────┐
│ Presign │
│   API   │ ───► { preSignedUrl, key }
└────┬────┘
     │
     │ PUT preSignedUrl (file bytes)
     ▼
┌─────┐
│ S3   │
└──┬──┘
   │
   │ POST /v1/jobs/parse-job
   │ { resumeParserData: { detailfilename: { key, name } }, onlyParserData: true }
   │ Bearer token
   ▼
┌─────────┐
│  Parse  │
│   API   │ ───► { data: { job: { ... } } }
└─────────┘
```

---

## 8. Checklist for Implementation

- [ ] Config for BASE_URL, credentials, test JD path
- [ ] Login test (success and failure)
- [ ] Presign test (success, 401 without token)
- [ ] Parse test (with mocked/stubbed key if needed)
- [ ] Full E2E: Login → Presign → Upload → Parse
- [ ] Field extraction validation for at least one known JD
- [ ] `job_document_id` format validation
- [ ] Error handling for 401, invalid key, missing file
- [ ] Support for PDF, DOCX, TXT at minimum (DOC, PNG optional)
- [ ] CI-friendly test runner (e.g. pytest)

---

*Generated from Job Parser Validator reference implementation.*
