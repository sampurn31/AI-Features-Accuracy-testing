# Nested Custom Fields Test Resume

## Overview

**File:** `006_alex_kumar_nested.pdf`  
**Test Case ID:** `TC006_NESTED`  
**Candidate:** Alex Kumar - Senior Backend Engineer

## Resume Content Strategy

The resume is written in **natural, parser-friendly prose** where all nested custom field values appear as complete sentences, making them easy for the AI parser to extract without ambiguity.

### Example Excerpts from Resume:

**Work Preferences Section:**
```
I am open to traveling regularly for client work or team meetings, typically 
within a 200 mile radius of my base location. I prefer Remote work arrangements 
and have set up a Dedicated room in my home for focused engineering work.

My technical expertise spans both Backend and Data engineering. For backend 
development, I primarily work with Django and FastAPI frameworks. On the data 
side, I am proficient with Apache Spark and Airflow for pipeline orchestration.

I am available to start in 4 weeks and typically work on a contract basis 
with a preferred rate of $150/hour for independent consulting projects.
```

## Ground Truth Mapping

### Parent Fields (columns 1-3)

| Column | Field Name | Type | Value | Notes |
|--------|------------|------|-------|-------|
| 1 | Willing to Travel | checkbox | `"1"` | Explicitly states "open to traveling regularly" |
| 2 | Work Mode Preference | dropdown | `"Remote"` | States "I prefer Remote work arrangements" |
| 3 | Technical Skill Focus | multiselect | `["Backend", "Data"]` | Lists both areas explicitly |

### Child Fields (columns 4-8)

| Column | Field Name | Type | Value | Parent | Visibility Rule |
|--------|------------|------|-------|--------|----------------|
| 4 | Travel Radius | text | `"200 mile radius"` | col1 | ✅ Visible (col1=1) |
| 5 | Office Setup Type | dropdown | `"Dedicated room"` | col2 | ✅ Visible & filtered (Remote → Dedicated room, Shared space) |
| 6 | Onsite Location | text | `null` | col2 | ❌ Hidden (Remote → col6 hidden) |
| 7 | Backend Stack | multiselect | `["Django", "FastAPI"]` | col3 | ✅ Visible (Backend+Data → union filter) |
| 8 | Data Tool Preference | text | `"Apache Spark and Airflow"` | col3 | ✅ Visible (Data selected) |

### Independent Fields (columns 9-10)

| Column | Field Name | Type | Value | Notes |
|--------|------------|------|-------|-------|
| 9 | Notice Period | number | `"4"` | States "available to start in 4 weeks" |
| 10 | Preferred Contract Rate | text | `"$150/hour"` | States "$150/hour for independent consulting" |

## Dependency Validation Scenarios

### ✅ Valid Dependencies

1. **Travel Radius (col4)** is visible because checkbox parent (col1) = 1
2. **Office Setup Type (col5)** shows "Dedicated room" which is in the allowed set for Remote: `{Dedicated room, Shared space}`
3. **Backend Stack (col7)** shows Django & FastAPI, both valid in union of Backend+Data filters
4. **Data Tool Preference (col8)** is visible because Data is selected in multiselect parent

### ❌ Hidden Field

1. **Onsite Location (col6)** is correctly hidden because Work Mode = Remote (only visible when Onsite)

## Parser Testing Guidelines

When testing with this resume:

1. **Standard fields** should parse correctly (name, email, position, company, etc.)
2. **Parent fields** (col1-3) should extract exact values from natural language
3. **Child fields** should respect visibility rules:
   - col4: Extract text if parser understands "willing to travel"
   - col5: Should extract only if parser recognizes Remote → allows this dropdown option
   - col6: Should return `null` or not extract (hidden case)
   - col7: Extract multiselect array from "Django and FastAPI"
   - col8: Extract text description from data tools sentence
4. **Independent fields** (col9-10) should always extract regardless of other values

## Expected Parser Output

```json
{
  "custcolumn1": "1",
  "custcolumn2": "Remote",
  "custcolumn3": ["Backend", "Data"],
  "custcolumn4": "200 mile radius",
  "custcolumn5": "Dedicated room",
  "custcolumn6": null,
  "custcolumn7": ["Django", "FastAPI"],
  "custcolumn8": "Apache Spark and Airflow",
  "custcolumn9": "4",
  "custcolumn10": "$150/hour"
}
```

## Validation Notes

The nested validation pipeline should:

1. ✅ Match col1-3 parent values exactly
2. ✅ Validate col4 exists because parent checkbox = 1
3. ✅ Validate col5 value is in Remote's allowed set
4. ✅ Confirm col6 is null/empty (correct hidden state)
5. ✅ Validate col7 options are in union of Backend+Data filters
6. ✅ Validate col8 exists because Data is selected
7. ✅ Match col9-10 independent values

## Files Generated

- **PDF Resume:** `test_data/resumes/006_alex_kumar_nested.pdf`
- **Ground Truth:** Added to `test_data/ground_truth.json` under `nested_test_cases` array
- **Generator Script:** `generate_nested_resume.py` (can be run again to regenerate)
