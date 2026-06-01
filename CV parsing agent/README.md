# CV Parsing Agent — Accuracy Testing Suite

Automated pipelines for testing and validating Recruit CRM's resume parser across standard fields, custom fields, nested dependencies, multilingual resumes, and EEO guardrails.

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure credentials
```bash
cp .env.example .env
# Fill in all values in .env
```

All credentials and environment URLs live in `.env`. The file `config.py` loads them — **never edit credentials directly in Python files**.

---

## Scripts

### Core comparison

| Script | What it does | Run |
|--------|-------------|-----|
| `Comparator.py` | Field-by-field comparison of Sovren parser vs InHouse LLM parser across all standard fields. Outputs `Parser_Accuracy_Report.xlsx`. | `python3 Comparator.py` |
| `bulk_resume_parser_logger.py` | Parses all 100 resumes, logs standard fields + response time to `bulk_parse_output/bulk_parse_report.xlsx`. No comparison. | `python3 bulk_resume_parser_logger.py` |
| `standard_field_parity.py` | Parses originals on Track A and Track B, compares standard fields side-by-side. Outputs `pipeline_output/parity_report.xlsx`. | `python3 standard_field_parity.py` |

### Custom field pipelines

| Script | What it does | Run |
|--------|-------------|-----|
| `create_custom_fields.py` | **Run once** — creates 10 custom fields on the SS environment and writes `pipeline_output/custom_fields_manifest.json`. | `python3 create_custom_fields.py` |
| `custom_field_pipeline.py` | Main pipeline: Track A baseline parse → Groq LLM injects custom-field snippets → Track B re-parses → validates extraction accuracy. Outputs `pipeline_output/validation_report.xlsx`. | `python3 custom_field_pipeline.py` |
| `ab_comparison_pipeline.py` | Parses the same LLM-modified resume on both Track A and Track B and compares results. Outputs `pipeline_output/ab_comparison_report.xlsx`. | `python3 ab_comparison_pipeline.py` |
| `injection_preview.py` | Preview LLM snippet injections before running the full pipeline. Saves `.txt` files to `preview_output/` for inspection. | `python3 injection_preview.py` |

### Nested / dependent custom fields

| Script | What it does | Run |
|--------|-------------|-----|
| `nested_custom_field_pipeline.py` | Tests 10 fields with parent-child dependencies (3-level max). Requires `--setup` on first run to create fields. Outputs `pipeline_output/nested/nested_validation_report.xlsx`. | `python3 nested_custom_field_pipeline.py [--setup] [--reparse]` |
| `deep_nested_field_pipeline.py` | Tests 5-level dependency chains (Chain A) and 3-level chains (Chain B). Uses a separate SS test account. Outputs `pipeline_output/deep_nested/deep_nested_validation_report.xlsx`. | `python3 deep_nested_field_pipeline.py [--setup] [--reparse]` |

### Multilingual pipelines

All multilingual pipelines follow the same flow: Track A baseline → LLM generates language-specific snippets → Track B re-parses → validation report with semantic RCA for failed rows.

Resumes live in `multilingual/<Language>/`. Outputs go to `pipeline_output/<language>/`.

| Script | Language | Model |
|--------|----------|-------|
| `multilingual_pipeline_german.py` | German | `gpt-oss-120b` |
| `multilingual_pipeline_spanish.py` | Spanish | `gpt-oss-120b` |
| `multilingual_pipeline_dutch.py` | Dutch | `gpt-oss-120b` |
| `multilingual_pipeline_hebrew.py` | Hebrew | `gpt-oss-120b` |
| `multilingual_pipeline_japanese.py` | Japanese | `gpt-oss-120b` |

```bash
python3 multilingual_pipeline_german.py
```

### EEO guardrail

| Script | What it does | Run |
|--------|-------------|-----|
| `eeo_guardrail_test_suite.py` | 150+ test cases across 8 categories (direct EEO attributes, semantic variants, multilingual protected fields, prompt injection attempts, toggle behaviour). Outputs `pipeline_output/eeo_guardrail/eeo_guardrail_test_report.xlsx`. | `python3 eeo_guardrail_test_suite.py [--regenerate] [--dry-run]` |

### Test data generation

| Script | What it does | Run |
|--------|-------------|-----|
| `generate_test_data.py` | Generates 5 synthetic resume PDFs with embedded custom-field ground truth. Output: `test_data/`. | `python3 generate_test_data.py` |
| `all_field_types_inject.py` | Generates 6 PDFs covering all 10 custom field types. Output: `pipeline_output/injected_pdfs/`. | `python3 all_field_types_inject.py` |

---

## Environments

| Name | Purpose | Config key prefix |
|------|---------|------------------|
| Track A | Baseline (existing) parser | `TRACK_A_*` |
| Track B / SS | AI parser under test | `TRACK_B_*` |
| Comparator | Sovren vs InHouse comparison | `COMPARATOR_*`, `SOVREN_*`, `INHOUSE_*` |

Multilingual and nested pipelines use separate test accounts to avoid field name collisions — see `.env.example` for the full list.

---

## Output structure

```
pipeline_output/
  validation_report.xlsx        # custom_field_pipeline
  ab_comparison_report.xlsx     # ab_comparison_pipeline
  parity_report.xlsx            # standard_field_parity
  nested/                       # nested_custom_field_pipeline
  deep_nested/                  # deep_nested_field_pipeline
  german|spanish|dutch|hebrew|japanese/   # multilingual pipelines
  eeo_guardrail/                # eeo_guardrail_test_suite
bulk_parse_output/              # bulk_resume_parser_logger
logs/                           # all pipeline logs
```

All output directories are in `.gitignore` — they are fully reproducible by re-running the scripts.
