# TC007_INVALID_DEPENDENCY - Invalid Nested Custom Field Resume

## Overview

This test case is specifically designed to **violate a dependency rule** and verify that the AI parser correctly respects nested custom field dependencies by **not extracting values** for fields that should be hidden based on their parent field values.

## Resume Details

- **File**: `007_mary_jones_invalid.pdf`
- **Candidate**: Mary Jones
- **Test Case ID**: `TC007_INVALID_DEPENDENCY`

## Dependency Violation Scenario

### The Setup

According to the dependency rules in `nested_custom_field_pipeline.py`:

```python
DEPENDENCY_RULES = {
    6: {   # Onsite Location ← dropdown parent
        "parent_col": 2,  # Work Mode Preference
        "parent_type": "dropdown",
        "child_type": "text",
        "child_option_filter": {
            "Onsite": "ALL",   # Visible when Onsite
            "Remote": None,    # ← HIDDEN when Remote
            "Hybrid": None,    # Hidden when Hybrid
        },
    },
}
```

### The Violation

1. **Parent Field** (`custcolumn2` - Work Mode Preference): `"Remote"`
2. **Child Field** (`custcolumn6` - Onsite Location): Should be **HIDDEN** (not visible)
3. **Resume Text**: Contains "Previously worked at the San Francisco office..."
4. **Expected Ground Truth**: `custcolumn6: null`

### Why This is a Violation

Even though the resume text explicitly mentions a location ("San Francisco office"), the parser should **NOT** extract this value into `custcolumn6` because:

- The parent field (Work Mode Preference) is set to "Remote"
- According to the dependency rules, when Work Mode = "Remote", the Onsite Location field should be hidden
- Hidden fields should not be extracted, regardless of whether relevant text exists in the resume

## Ground Truth Mapping

### Standard Fields
```json
{
  "firstname": "Mary",
  "lastname": "Jones",
  "email": "mary.jones@techmail.com",
  "phone": "+1 650 555 0123"
}
```

### Nested Custom Fields

| Column | Field Name | Expected Value | Visibility Status |
|--------|-----------|----------------|-------------------|
| custcolumn1 | Willing to Travel | `"1"` | Independent parent field |
| custcolumn2 | Work Mode Preference | `"Remote"` | **Parent controlling col5 & col6** |
| custcolumn3 | Technical Skill Focus | `["Backend", "Data"]` | Parent controlling col7 & col8 |
| custcolumn4 | Travel Radius | `"100 miles"` | Visible (parent col1 = "1") |
| custcolumn5 | Office Setup Type | `"Dedicated room"` | Visible (Remote allows this option) |
| custcolumn6 | **Onsite Location** | **`null`** | **HIDDEN** (Remote makes it invisible) |
| custcolumn7 | Backend Stack | `["Django", "FastAPI"]` | Visible (Backend+Data selected) |
| custcolumn8 | Data Tool Preference | `"Apache Spark"` | Visible (Data selected) |
| custcolumn9 | Notice Period | `30` | Independent field |
| custcolumn10 | Preferred Contract Rate | `"$95/hour"` | Independent field |

## Validation Scenarios

### 1. Parent Field Validation (custcolumn2)
- **Field**: Work Mode Preference
- **Expected**: `"Remote"`
- **Validation**: Should extract exactly "Remote" from resume text

### 2. Dependency Violation Field (custcolumn6)
- **Field**: Onsite Location
- **Expected**: `null`
- **Resume Contains**: "San Francisco office"
- **Validation**: Should **NOT** extract any value, must be `null` or missing
- **Reason**: Parent field (Remote) makes this field hidden

### 3. Other Visible Child Fields
- **custcolumn4** (Travel Radius): Should extract "100 miles" (parent checkbox = "1")
- **custcolumn5** (Office Setup Type): Should extract "Dedicated room" (allowed for Remote)
- **custcolumn7** (Backend Stack): Should extract `["Django", "FastAPI"]` (Backend+Data selected)
- **custcolumn8** (Data Tool Preference): Should extract "Apache Spark" (Data selected)

## Expected Parser Behavior

### ✅ Correct Behavior
```json
{
  "custcolumn1": "1",
  "custcolumn2": "Remote",
  "custcolumn3": ["Backend", "Data"],
  "custcolumn4": "100 miles",
  "custcolumn5": "Dedicated room",
  "custcolumn6": null,  // ← Correctly NOT extracted
  "custcolumn7": ["Django", "FastAPI"],
  "custcolumn8": "Apache Spark",
  "custcolumn9": 30,
  "custcolumn10": "$95/hour"
}
```

### ❌ Incorrect Behavior
```json
{
  "custcolumn6": "San Francisco"  // ← WRONG: Should not extract this
}
```

## Testing Guidelines

1. **Parse the resume** using the standard parsing API
2. **Extract custom fields** from the parsed response
3. **Check custcolumn6**: It must be `null`, empty, or missing (not "San Francisco" or any location)
4. **Verify other fields**: All other fields should parse correctly according to ground truth

## API Automation Assertions

```python
# After parsing the resume
parsed_custom_fields = response['data']['candidate']['custom_fields']

# Critical assertion for dependency violation
assert parsed_custom_fields.get('custcolumn6') is None, \
    "custcolumn6 should be null because Work Mode=Remote makes it hidden"

# Verify parent field
assert parsed_custom_fields.get('custcolumn2') == 'Remote', \
    "Parent field should be extracted correctly"

# Verify other visible child fields
assert parsed_custom_fields.get('custcolumn4') == '100 miles'
assert parsed_custom_fields.get('custcolumn5') == 'Dedicated room'
```

## Key Testing Points

1. **Dependency Rules Are Enforced**: Parser must respect parent-child visibility rules
2. **Text Presence ≠ Extraction**: Just because text exists doesn't mean it should be extracted
3. **Hidden Means Hidden**: When a field is hidden by its parent, it should not be extracted regardless of text
4. **Partial Extraction is Wrong**: Extracting 9 out of 10 fields correctly but including the hidden field is still a failure

## Real-World Scenario

This tests a common real-world scenario:
- A candidate mentions past office experience in their resume
- But their current preference is "Remote" work
- The parser should understand that "Onsite Location" is not relevant when "Remote" is selected
- This prevents incorrect data from being stored in fields that shouldn't be visible

## Success Criteria

✅ **Test Passes** if:
- `custcolumn6` is `null`, empty, or missing in the parsed output
- All other custom fields (custcolumn1-5, 7-10) are correctly extracted

❌ **Test Fails** if:
- `custcolumn6` contains any value (e.g., "San Francisco", "SF", etc.)
- This indicates the parser is not respecting dependency rules
