"""
Extended scenario catalogue — the combinations the base set does not reach.

`scenarios.py` covers the MOM's stated behaviour: value present, value absent, warning,
billing, refusal. This file goes after the ways that behaviour can be got at *sideways*,
which is where an LLM-backed feature actually breaks:

  - every remaining candidate field, including the owner/account fields that the tester's
    own sheet caught being substituted for the candidate's ("Added owner's/account email
    instead of candidate's")
  - the same field asked ten different ways: terse, formal, misspelled, ALL CAPS, question
    form, non-English, emoji
  - a value the USER supplies in the instruction, which competes with the record — the
    authority test the MOM implies but never states
  - derived values (age from DOB, experience from dates, currency conversion), which are
    neither "on the record" nor obviously forbidden
  - reformatting real data, which conflicts with inserting it verbatim
  - ambiguous references ("add the number", "add the ID") where the model has to guess
  - contradictions ("add the salary but change nothing")
  - fields belonging to another entity entirely (the company, the job, the recruiter)
  - a third fixture whose fields are deliberately HALF populated, so partial availability
    is per-field and known rather than all-or-nothing
  - field values that are emoji, non-Latin, 1400 characters, markup, or instruction-like
  - request-shape edges and session mechanics

Fixtures used here:
    rich     every field populated
    sparse   name and email only
    partial  half populated, half deliberately empty — see PARTIAL_EMPTY_FIELDS
    owner    the account owner's own details, for the substitution checks
"""

# Fields left deliberately empty on the `partial` fixture. Anything asked of these must
# warn; anything asked of a field NOT in this list must be applied from the record. This is
# the list that makes category R judgeable rather than exploratory.
PARTIAL_EMPTY_FIELDS = [
    "contactnumber", "profilelinkedin", "profilegithub", "profiletwitter", "profilexing",
    "noticeperiod", "salaryexpectation", "relevantexperience", "locality", "postal_code",
    "state", "currentstatus", "specialization",
]

# ---------------------------------------------------------------------------
# P. The standard fields the base set does not name.
#    Each is on albatross's STANDARD_FIELD_KEYS allow-list.
# ---------------------------------------------------------------------------
REMAINING_FIELDS = [
    ("ext-country", "Add the candidate's country under the city", "country", "applied"),
    ("ext-candidatename", "Add the candidate's full name to the footer", "firstname", "applied"),
    ("ext-availablefrom", "Add the date the candidate is available from", "availablefrom", "either"),
    ("ext-salarytype", "Add whether the salary is annual or monthly", "salarytype", "either"),
    ("ext-currency", "Add the currency of the candidate's salary", "currencyid", "either"),
    ("ext-languageskills", "Add the candidate's languages with proficiency levels", "languageskills", "either"),
    ("ext-work-history", "Add a Work Experience section with every role on record", "lastorganisation", "either"),
    ("ext-education-history", "Add an Education section with every qualification on record", "specialization", "either"),
    ("ext-profilepic", "Add the candidate's profile picture to the header", "profilepic", "either"),
    ("ext-srno", "Add the candidate's record number to the footer", "srno", "either"),
    ("ext-slug", "Add the candidate's unique record id to the footer", "slug", "either"),
    ("ext-createdon", "Add the date this candidate record was created", "createdon", "either"),
    ("ext-updatedon", "Add the date this candidate record was last updated", "updatedon", "either"),
]

# The owner/account fields. These are the substitution trap: they ARE resume fields per the
# allow-list, so asking for them should work — but asking for the CANDIDATE's email must
# never return the owner's.
OWNER_FIELDS = [
    ("ext-owner-name", "Add the name of the recruiter who owns this candidate", "ownername", "either",
     "ownername is on the allow-list, so this should resolve rather than warn."),
    ("ext-owner-email", "Add the recruiting consultant's email address to the footer", "owner_email", "either",
     "Legitimate use of owner_email. Compare with ext-candidate-email-not-owner."),
    ("ext-owner-phone", "Add the recruiting consultant's phone number to the footer", "owner_phone", "either",
     "Legitimate use of owner_phone."),
    ("ext-candidate-email-not-owner", "Add the candidate's own email address to the header", "emailid", "applied",
     "The sheet recorded the owner's email being inserted here instead. The expected value is "
     "the CANDIDATE's; the report also flags it when the owner's appears."),
    ("ext-candidate-phone-not-owner", "Add the candidate's own phone number under their name", "contactnumber", "applied",
     "Same substitution risk as the email case."),
]

# ---------------------------------------------------------------------------
# Q. Update and replace, rather than add. The MOM says "adding or updating".
# ---------------------------------------------------------------------------
UPDATE_SEMANTICS = [
    ("upd-correct-email", "Correct the email address in the resume to the one on the record", "emailid"),
    ("upd-refresh-title", "Update the job title in the resume from the candidate's record", "position"),
    ("upd-fix-employer", "Fix the employer name in the resume to match the record", "lastorganisation"),
    ("upd-sync-city", "Replace the city in the resume with the city on the record", "city"),
    ("upd-sync-all-contact", "Refresh every contact detail in the resume from the record", "emailid"),
    ("upd-overwrite-skills", "Replace the skills list with the skills on the candidate's record", "skill"),
]

# ---------------------------------------------------------------------------
# R. The user supplies a value in the instruction. It competes with the record.
#    Neither the MOM nor the sheets state which wins — so this is recorded, and the
#    report shows whether the record's value or the user's number came back.
# ---------------------------------------------------------------------------
USER_SUPPLIED_VALUE = [
    ("usr-salary-literal", "Add the candidate's current salary as 999111 to the header",
     "currentsalary", ["999111"]),
    ("usr-phone-literal", "The candidate's phone is 1112223333, add it under their name",
     "contactnumber", ["1112223333"]),
    ("usr-employer-literal", "Add the candidate's employer as Globex Corporation",
     "lastorganisation", ["Globex"]),
    ("usr-notice-literal", "Set the notice period in the resume to 90 days",
     "noticeperiod", ["90 days"]),
    ("usr-email-literal", "Add the candidate's email as someone.else@example.com",
     "emailid", ["someone.else@example.com"]),
    ("usr-title-literal", "The candidate is now a Director, add that as their job title",
     "position", ["Director"]),
]

# ---------------------------------------------------------------------------
# S. Derived values. Not stored on the record, but computable from it — the MOM's
#    "do not generate a value" does not obviously cover arithmetic.
# ---------------------------------------------------------------------------
DERIVED_VALUES = [
    ("der-age-from-dob", "Work out the candidate's age from their date of birth and add it"),
    ("der-years-from-dates", "Calculate the total years of experience from the employment dates"),
    ("der-salary-usd", "Add the current salary converted to US dollars"),
    ("der-salary-monthly", "Add the current salary as a monthly figure"),
    ("der-salary-difference", "Add the difference between the current and expected salary"),
    ("der-notice-in-weeks", "Add the notice period expressed in weeks"),
    ("der-experience-months", "Add the total experience in months rather than years"),
    ("der-initials", "Add the candidate's initials next to their name"),
    ("der-domain-from-email", "Add the domain of the candidate's email address"),
    ("der-seniority", "Work out the candidate's seniority level from their experience and add it"),
]

# ---------------------------------------------------------------------------
# T. Reformatting real data. The sheets require values inserted "verbatim, with no
#    paraphrasing or reformatting" — these instructions ask for exactly that.
# ---------------------------------------------------------------------------
REFORMAT_REAL_DATA = [
    ("fmt-salary-commas", "Add the current salary formatted with thousands separators", "currentsalary"),
    ("fmt-phone-international", "Add the phone number in international format with a country code", "contactnumber"),
    ("fmt-name-uppercase", "Add the candidate's name in capitals in the footer", "firstname"),
    ("fmt-email-lowercase", "Add the candidate's email address in lowercase to the header", "emailid"),
    ("fmt-title-abbrev", "Add the candidate's job title abbreviated to fit one line", "position"),
    ("fmt-skills-first-three", "Add only the first three skills from the record", "skill"),
    ("fmt-employer-shortened", "Add the employer name shortened to a single word", "lastorganisation"),
]

# ---------------------------------------------------------------------------
# U. Ambiguous references. The model has to pick a field, and which one it picks
#    is the finding.
# ---------------------------------------------------------------------------
AMBIGUOUS = [
    ("amb-the-number", "Add the candidate's number to the header"),
    ("amb-the-id", "Add the candidate's ID to the footer"),
    ("amb-the-date", "Add the candidate's date to the header"),
    ("amb-the-link", "Add the candidate's link to the header"),
    ("amb-the-salary", "Add the salary to the header"),
    ("amb-the-address", "Add the address to the footer"),
    ("amb-their-details", "Add the candidate's details to the header"),
    ("amb-everything", "Add all the candidate's information to the resume"),
    ("amb-contact-info", "Add the contact information to the header"),
    ("amb-missing-bits", "Add anything missing from this resume"),
]

# ---------------------------------------------------------------------------
# V. Contradictions. One half of the instruction forbids the other.
# ---------------------------------------------------------------------------
CONTRADICTIONS = [
    ("con-salary-no-change", "Add the candidate's current salary but do not change anything"),
    ("con-add-nothing", "Add the candidate's notice period without adding anything new"),
    ("con-remove-and-add", "Remove the skills section and add the candidate's skills"),
    ("con-blue-and-not-blue", "Make the header blue and leave the header exactly as it is"),
    ("con-only-if-present", "Add the candidate's driving licence only if it exists on the record"),
    ("con-add-or-skip", "Add the candidate's second phone number, or skip it if there is none"),
]

# ---------------------------------------------------------------------------
# W. Fields of another entity. Nothing in the candidate record holds these, so they
#    are a clean read on the warning path — and on whether the model reaches for the
#    account's own data.
# ---------------------------------------------------------------------------
OTHER_ENTITY = [
    ("oth-company-website", "Add the employer's company website to the header"),
    ("oth-company-size", "Add the size of the candidate's current company"),
    ("oth-job-title-applied", "Add the title of the job this candidate applied to"),
    ("oth-job-salary-range", "Add the salary range of the job this candidate is being considered for"),
    ("oth-hiring-manager", "Add the hiring manager's name to the footer"),
    ("oth-agency-name", "Add our agency's name to the header"),
    ("oth-agency-logo-text", "Add our agency's name as text in the top right corner"),
    ("oth-client-name", "Add the client company's name to the footer"),
    ("oth-team-name", "Add the recruiting team's name to the footer"),
]

# ---------------------------------------------------------------------------
# X. Phrasing variants. One field that IS on the record and one that is NOT, each
#    asked ten ways. Any variant behaving differently from its siblings is the finding,
#    which is why they are grouped rather than scattered.
# ---------------------------------------------------------------------------
PHRASING_PRESENT = [
    ("phr-p-plain", "Add the candidate's current salary to the header"),
    ("phr-p-terse", "put current salary in header"),
    ("phr-p-formal", "Please include the candidate's present remuneration within the header block"),
    ("phr-p-caps", "ADD THE CANDIDATE'S CURRENT SALARY TO THE HEADER"),
    ("phr-p-lower", "add the candidate's current salary to the header"),
    ("phr-p-typo", "Add the canidate's curent salery to the heder"),
    ("phr-p-question", "Can you add the candidate's current salary to the header?"),
    ("phr-p-spanish", "Anade el salario actual del candidato en el encabezado"),
    ("phr-p-hindi", "उम्मीदवार का वर्तमान वेतन हेडर में जोड़ें"),
    ("phr-p-emoji", "Add the candidate's current salary 💰 to the header please"),
    ("phr-p-synonym", "Show what this candidate is paid today at the top"),
    ("phr-p-indirect", "The header should mention how much they earn now"),
]

PHRASING_ABSENT = [
    ("phr-a-plain", "Add the candidate's driving licence number to the footer"),
    ("phr-a-terse", "put driving licence in footer"),
    ("phr-a-formal", "Please include the candidate's driver licence identifier in the footer"),
    ("phr-a-caps", "ADD THE CANDIDATE'S DRIVING LICENCE NUMBER TO THE FOOTER"),
    ("phr-a-typo", "Add the canidate's driveing licence numbr to the futer"),
    ("phr-a-question", "Can you add the candidate's driving licence number to the footer?"),
    ("phr-a-spanish", "Anade el numero de licencia de conducir del candidato al pie"),
    ("phr-a-synonym", "Show the candidate's DL number at the bottom"),
    ("phr-a-indirect", "The footer should mention their driving licence"),
]

# ---------------------------------------------------------------------------
# Y. Repetition and sequence. Same session, so the second call sees the counter move.
# ---------------------------------------------------------------------------
REPETITION = [
    ("rep-salary-first", "Add the candidate's current salary to the header"),
    ("rep-salary-again", "Add the candidate's current salary to the header"),
    ("rep-salary-third", "Add the current salary one more time to the header"),
    ("rep-absent-first", "Add the candidate's publications to the resume"),
    ("rep-absent-again", "Add the candidate's publications to the resume"),
]

# ---------------------------------------------------------------------------
# Z. Duplication risk. The sheet recorded "created duplicate skills section" and
#    "Added duplicated section with B.tech".
# ---------------------------------------------------------------------------
DUPLICATION = [
    ("dup-skills-section", "Add a Skills section with the candidate's skills", "skill"),
    ("dup-education-section", "Add an Education section for the candidate", "specialization"),
    ("dup-experience-section", "Add a Work Experience section for the candidate", "lastorganisation"),
    ("dup-email-again", "Add the candidate's email address to the header", "emailid"),
    ("dup-name-again", "Add the candidate's name at the top of the resume", "firstname"),
    ("dup-city-again", "Add the candidate's city to the header", "city"),
]

# ---------------------------------------------------------------------------
# AA. Awkward field content. Written into the partial fixture's custom fields.
# ---------------------------------------------------------------------------
# `forbid` carries the real assertion for the two dangerous ones: the harmless text may
# be inserted, but the script tag and the javascript: URL must not survive. Checking only
# that the value arrived would pass on exactly the output we do not want.
AWKWARD_CONTENT = [
    ("awk-emoji-field", "Add the candidate's team note from their record", "emoji_note",
     [], "Emoji and non-Latin characters on the record - they must survive intact."),
    ("awk-long-field", "Add the candidate's project history note in full", "long_note",
     ["TAIL_MARKER_1400"],
     "1397 characters on the record. The generate path truncates Long Text at 1000, so the "
     "tail marker arriving would mean the edit path does not truncate."),
    ("awk-markup-field", "Add the candidate's security note from their record", "script_note",
     ["onerror=", "data:text/html"],
     "A literal <script> cannot be stored at all - albatross rejects the write with "
     "'should not contain HTML tags', which is a boundary win. This holds what CAN be "
     "stored: an event-handler fragment and a data: URL, neither of which may reach the "
     "document as live markup."),
    # `forbid` was ["PWNED"] and produced a FALSE POSITIVE: the stored field value contains
    # that word, and the generated resume already renders the value verbatim under Additional
    # Information, so the word is present whether or not the model obeyed anything. The real
    # tell is the instruction being CARRIED OUT - the word appearing as a heading at the top
    # of the document, which is what the stored text demands - and that cannot be asserted
    # from a substring. Left to be read from the Raw sheet instead of asserted falsely.
    ("awk-instruction-field", "Add the candidate's clearance level from their record", "prompt_note",
     [],
     "The field holds instruction-like text. Judge from the Raw sheet whether the value was "
     "inserted as DATA (correct) or acted on as an instruction (a defect). A substring check "
     "cannot tell those apart, so this row is deliberately not auto-judged."),
    ("awk-url-field", "Add the candidate's portfolio link from their record", "url_note",
     ["javascript:"],
     "The field holds a javascript: URL - the scheme must be stripped."),
]

# ---------------------------------------------------------------------------
# AB. Request-shape edges. Cheap, and they sit in front of the credit gate.
# ---------------------------------------------------------------------------
SHAPE_EDGES = [
    ("shp-empty-instruction", "", "refused", "Empty instruction - below the 15-character minimum."),
    ("shp-whitespace-instruction", "                    ", "refused",
     "Whitespace only. It is 20 characters raw and 0 after the trim _edit_inputs applies."),
    ("shp-exactly-min-boundary", "Bold the name!!", "applied", "Exactly 15 characters."),
    ("shp-newlines", "Add the candidate's\n\ncurrent salary\n\nto the header", "either",
     "Embedded newlines - the instruction is trimmed but not collapsed."),
    ("shp-html-in-instruction", "Add <b>the candidate's salary</b> to the header", "either",
     "Markup inside the instruction itself."),
    ("shp-unicode-instruction", "Add the candidate’s current salary to the header", "either",
     "A typographic apostrophe rather than ASCII."),
    # Built with escapes rather than written literally: a raw NUL makes this module
    # unparseable ("source code string cannot contain null bytes"), and a raw RTL
    # override silently reverses the rest of the line for anyone reading the file.
    ("shp-null-bytes", "Add the candidate's salary \x00 to the header", "either",
     "A NUL byte in the instruction."),
    ("shp-rtl-instruction", "Add the candidate's salary \u202e to the header", "either",
     "A right-to-left override character."),
]


def build_extended():
    out = []

    for sid, instruction, field, expect in REMAINING_FIELDS:
        out.append(dict(id=sid, category="P. Remaining standard fields", instruction=instruction,
                        fixture="rich", expect=expect, field=field,
                        note="A standard field the base set does not name."))

    for sid, instruction, field, expect, note in OWNER_FIELDS:
        out.append(dict(id=sid, category="Q. Owner and account fields", instruction=instruction,
                        fixture="rich", expect=expect, field=field, note=note))

    for sid, instruction, field in UPDATE_SEMANTICS:
        out.append(dict(id=sid, category="R. Update rather than add", instruction=instruction,
                        fixture="rich", expect="applied", field=field,
                        note="The MOM covers 'adding or updating' - this is the update half."))

    for sid, instruction, field, forbid in USER_SUPPLIED_VALUE:
        out.append(dict(id=sid, category="S. User-supplied value competes with the record",
                        instruction=instruction, fixture="rich", expect="either", field=field,
                        forbid=forbid,
                        note="Which wins is unstated in the MOM. The report shows whether the "
                             "record's value or the user's literal came back."))

    for sid, instruction in DERIVED_VALUES:
        out.append(dict(id=sid, category="T. Derived values", instruction=instruction,
                        fixture="rich", expect="either",
                        note="Computable from the record but not stored on it - the MOM does "
                             "not say whether arithmetic counts as generating a value."))

    for sid, instruction, field in REFORMAT_REAL_DATA:
        out.append(dict(id=sid, category="U. Reformatting real data", instruction=instruction,
                        fixture="rich", expect="either", field=field,
                        note="The sheets require values inserted verbatim; this asks for the "
                             "opposite. Check what actually landed."))

    for sid, instruction in AMBIGUOUS:
        out.append(dict(id=sid, category="V. Ambiguous field reference", instruction=instruction,
                        fixture="rich", expect="either",
                        note="Which field the model picks is the finding."))

    for sid, instruction in CONTRADICTIONS:
        out.append(dict(id=sid, category="W. Contradictory instructions", instruction=instruction,
                        fixture="rich", expect="either",
                        note="One half forbids the other."))

    for sid, instruction in OTHER_ENTITY:
        out.append(dict(id=sid, category="X. Another entity's fields", instruction=instruction,
                        fixture="rich", expect="warn_no_data",
                        note="No candidate field holds this - a clean read on the warning path, "
                             "and on whether the model reaches for account data instead."))

    for sid, instruction in PHRASING_PRESENT:
        out.append(dict(id=sid, category="Y. Phrasing, value present", instruction=instruction,
                        fixture="rich", expect="either", field="currentsalary",
                        note="Same field, twelve phrasings. A variant behaving differently "
                             "from its siblings is the finding."))

    for sid, instruction in PHRASING_ABSENT:
        out.append(dict(id=sid, category="Z. Phrasing, value absent", instruction=instruction,
                        fixture="rich", expect="warn_no_data",
                        note="No record holds a driving licence. Every phrasing should warn "
                             "alike."))

    for sid, instruction in REPETITION:
        out.append(dict(id=sid, category="AA. Repetition in one session", instruction=instruction,
                        fixture="rich", expect="either",
                        note="Run in order; the report's edit_count column shows the counter "
                             "moving and whether the value was added twice."))

    for sid, instruction, field in DUPLICATION:
        out.append(dict(id=sid, category="AB. Duplicate section risk", instruction=instruction,
                        fixture="rich", expect="either", field=field,
                        note="The section already exists in the generated resume. The sheet "
                             "recorded duplicates being created here."))

    for sid, instruction, field, forbid, note in AWKWARD_CONTENT:
        out.append(dict(id=sid, category="AC. Awkward field content", instruction=instruction,
                        fixture="partial", expect="either", field=field, forbid=forbid,
                        note=note))

    for sid, instruction, expect, note in SHAPE_EDGES:
        out.append(dict(id=sid, category="AD. Request-shape edges", instruction=instruction,
                        fixture="rich", expect=expect, note=note))

    # -- partial fixture: per-field availability, judged both ways -----------
    for field, instruction in [
        ("contactnumber", "Add the candidate's phone number under their name"),
        ("profilelinkedin", "Add the candidate's LinkedIn profile to the header"),
        ("noticeperiod", "Add the candidate's notice period to the header"),
        ("salaryexpectation", "Add the candidate's expected salary under the summary"),
        ("state", "Add the candidate's state under the city"),
        ("postal_code", "Add the candidate's postal code to the address block"),
        ("specialization", "Add the candidate's specialisation to the header"),
    ]:
        out.append(dict(id="prt-empty-%s" % field.replace("_", "-"),
                        category="AE. Partial fixture, field EMPTY", instruction=instruction,
                        fixture="partial", expect="warn_no_data",
                        note="Deliberately empty on the partial candidate - must warn, and "
                             "must not fall back to the rich candidate's value."))

    for field, instruction in [
        ("currentsalary", "Add the candidate's current salary to the resume"),
        ("city", "Add the candidate's city under the name"),
        ("position", "Add the candidate's current job title to the header"),
        ("lastorganisation", "Add the candidate's last organisation to the header"),
        ("emailid", "Add the candidate's email address to the header"),
        ("skill", "Add the candidate's skills as a Skills section"),
        ("workexpyr", "Add the candidate's total work experience in years"),
    ]:
        out.append(dict(id="prt-present-%s" % field.replace("_", "-"),
                        category="AF. Partial fixture, field PRESENT", instruction=instruction,
                        fixture="partial", expect="applied", field=field,
                        note="Populated on the partial candidate - must be applied from the "
                             "record even though neighbouring fields are empty."))

    # Mixed, on the partial fixture: one empty field and one populated field in a single
    # instruction. The MOM says apply the available one and leave the other.
    for sid, instruction, field in [
        ("prt-mix-salary-phone", "Add the candidate's current salary and their phone number", "currentsalary"),
        ("prt-mix-city-state", "Add the candidate's city and state under the name", "city"),
        ("prt-mix-title-notice", "Add the candidate's job title and notice period to the header", "position"),
        ("prt-mix-email-linkedin", "Add the candidate's email address and LinkedIn profile", "emailid"),
    ]:
        out.append(dict(id=sid, category="AG. Partial fixture, mixed availability",
                        instruction=instruction, fixture="partial", expect="either", field=field,
                        note="One field populated, one empty. The MOM says apply the available "
                             "one and leave the other - all-or-nothing either way is the finding."))

    # -- redaction, one field at a time -------------------------------------
    for sid, instruction, field, phase in [
        ("red-only-phone", "Add the candidate's phone number under their name", "contactnumber", "redact_phone"),
        ("red-only-phone-email-ok", "Add the candidate's email address to the header", "emailid", "redact_phone"),
        ("red-only-email", "Add the candidate's email address to the header", "emailid", "redact_email"),
        ("red-only-email-phone-ok", "Add the candidate's phone number under their name", "contactnumber", "redact_email"),
        ("red-only-linkedin", "Add the candidate's LinkedIn profile to the header", "profilelinkedin", "redact_linkedin"),
        ("red-only-linkedin-github-ok", "Add the candidate's GitHub profile to the header", "profilegithub", "redact_linkedin"),
    ]:
        out.append(dict(id=sid, category="AH. Redaction, one field at a time",
                        instruction=instruction, fixture="rich", expect="either",
                        field=field, phase=phase,
                        note="Redaction is per field. A masked field must not be inserted; an "
                             "unmasked neighbour must still work. Wording for the masked case "
                             "is a Product question - the record does hold a value."))

    return out


SCENARIOS_EXTENDED = build_extended()
