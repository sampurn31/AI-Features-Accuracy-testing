# Production vs Staging Parser Comparison

## ✅ Current Configuration

### 🚀 Comparison Mode — Same Resume, Two Environments

**What's Active:**
- ✅ Production vs Staging comparison (both environments running)
- ✅ Batch processing — 20 resumes processed in parallel
- ✅ Field-by-field validation and scoring
- ✅ Status codes and response times tracked
- ✅ Per-resume Excel sheets + summary dashboard

---

## 🔄 Environment Configuration

### Production (Reference Baseline)
- **URL**: `https://albatross.recruitcrm.io`
- **Login**: `PROD_EMAIL` / `PROD_PASSWORD` in `.env`
- **Purpose**: Ground truth — production parser output

### Staging (Under Test)
- **URL**: `https://albatross-staging.recruitcrm.net`
- **Login**: `STAGE_EMAIL` / `STAGE_PASSWORD` in `.env`
- **Purpose**: Validate that staging output matches production

To change accounts, edit `.env` (copy `.env.example` first).

---

## ⚡ Batch Processing Configuration

```python
BATCH_SIZE = 20   # resumes processed concurrently
```

**How It Works:**
1. Both Production and Staging parse each resume in parallel
2. Resumes are processed in batches of 20
3. Results compared field-by-field after each resume
4. Scores, verdicts and mismatches captured

---

## 📊 Scoring System

| Situation | Score |
|-----------|-------|
| Staging matches Production | 0 |
| Staging has extra data Production is missing | +1 |
| Staging is missing data or wrong | -1 |

### Verdict Thresholds
| Verdict | Condition |
|---------|-----------|
| ✅ PASS | Score ≥ 0 AND no critical failures |
| ⚠️  ACCEPTABLE | Score ≥ -3 AND no critical failures |
| ❌ FAIL | Score < -3 OR any critical field failed |

### Critical Fields (auto-FAIL if wrong/missing in Staging)
- `fullName`
- `email`
- `phone`
- `city`

---

## 📋 Fields Compared

| Field | Source |
|-------|--------|
| fullName | firstname + lastname |
| firstName | firstname |
| lastName | lastname |
| email | emailid |
| phone | contactnumber |
| city | city |
| state | state |
| country | country |
| currentTitle | position |
| currentOrg | lastorganisation |
| workExpYears | workexpyr |
| skills | skill |
| specialization | specialization |
| summary | summary (Yes/No) |
| workHistory | `/candidate-work` API |
| education | `/candidate-education` API |

---

## 📊 Excel Report Structure

### Summary Sheet (first sheet):
```
┌──────────────┬─────────────┬──────────────┬──────────────┬───────────────┬───────┬───────────┬──────────────────┬────────────────┐
│ Resume       │ Prod Status │ Prod Time(s) │ Stage Status │ Stage Time(s) │ Score │ Verdict   │ Critical Fails   │ Field Mismatch │
├──────────────┼─────────────┼──────────────┼──────────────┼───────────────┼───────┼───────────┼──────────────────┼────────────────┤
│ resume1.pdf  │ 200         │ 2.34         │ 200          │ 2.89          │  0    │ ✅ PASS   │ None             │ None           │
│ resume2.pdf  │ 200         │ 1.98         │ 200          │ 3.12          │ -2    │ ⚠️  ACC   │ None             │ skills, city   │
│ resume3.pdf  │ 200         │ 2.67         │ 200          │ 4.01          │ -5    │ ❌ FAIL   │ email            │ email, phone   │
└──────────────┴─────────────┴──────────────┴──────────────┴───────────────┴───────┴───────────┴──────────────────┴────────────────┘

Statistics:
✅ PASS:       XX / YY (XX.X%)
⚠️  ACCEPTABLE: XX / YY (XX.X%)
❌ FAIL:       XX / YY (XX.X%)
Avg Production Time: X.Xs
Avg Staging Time:    X.Xs
```

### Per-Resume Sheets (one per resume):
```
┌──────────────────┬──────────────┬──────────────┬─────────────────┬───────┬──────────┐
│ Field            │ Production   │ Staging      │ Match Type      │ Score │ Critical │
├──────────────────┼──────────────┼──────────────┼─────────────────┼───────┼──────────┤
│ fullName         │ John Doe     │ John Doe     │ Match           │  0    │          │
│ email            │ john@...     │ john@...     │ Match           │  0    │ ⚠️        │
│ skills           │ Python, SQL  │              │ Staging Missing │ -1    │          │
└──────────────────┴──────────────┴──────────────┴─────────────────┴───────┴──────────┘

Metadata:
  TOTAL SCORE:              -1
  VERDICT:                  ⚠️  ACCEPTABLE
  CRITICAL FAILURES:        None
  FIELD MISMATCHES:         skills

  PRODUCTION STATUS CODE:   200
  PRODUCTION RESPONSE TIME: 2.34s
  STAGING STATUS CODE:      200
  STAGING RESPONSE TIME:    2.89s
```

**Row colours:**
- 🟢 Green  = Match
- 🟡 Yellow = Staging Extra (more data than production)
- 🔴 Red    = Staging Missing / Mismatch

---

## 🚀 How to Run

```bash
cd "Resume Parser"
python3 Comparator.py
```

**What Happens:**
1. Authenticates with both Production and Staging
2. Discovers all resumes in `ground-truth-set/`
3. Processes in batches of 20 — each resume parsed by both environments in parallel
4. Compares all fields, calculates scores, determines verdicts
5. Generates `Parser_Comparison_Report.xlsx` with full breakdown

**Expected Duration:**
- ~2–5 minutes depending on number of resumes and API response times

---

## ⚙️ Changing Environments / Credentials

Credentials come from `.env`; URLs are in the `CONFIG` block at the top of `Comparator.py`:

```python
# Production  (reference baseline)
PROD_EMAIL    = os.getenv("PROD_EMAIL", "")
PROD_PASSWORD = os.getenv("PROD_PASSWORD", "")
PROD_BASE     = "https://albatross.recruitcrm.io"

# Staging  (under test)
STAGE_EMAIL    = os.getenv("STAGE_EMAIL", "")
STAGE_PASSWORD = os.getenv("STAGE_PASSWORD", "")
STAGE_BASE     = "https://albatross-staging.recruitcrm.net"
```
