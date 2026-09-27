"""
Triage decisions on findings already raised, so a re-run reports them as settled rather
than as fresh failures.

A check listed here still runs and is still measured — the status only changes how it is
counted. Silencing a check would mean the day the behaviour changes, nobody notices.

Statuses:
  PASS   — behaved as required
  FAIL   — open defect, someone needs to act
  KNOWN  — triaged and accepted (unreachable through the UI, or by design)
  INFO   — recorded for context, nothing asserted
"""

# check name -> (status, who decided what)
TRIAGED = {
    "double negation is handled": (
        "KNOWN",
        "Blocked in the UI - a recruiter cannot submit NOT NOT, so the 500 is not "
        "reachable in product. Kept as an API-level record."),
    "AND binds tighter than OR without parentheses": (
        "KNOWN",
        "By design - parentheses are how precedence is expressed in this search box, and "
        "the unparenthesised form is not claimed to follow boolean algebra."),
    "bare NOT returns nothing": (
        "KNOWN",
        "Blocked in the UI - a pure exclusion cannot be submitted on its own."),
}

# Open items, carried so the sheet says who holds each one.
OWNERS = {
    "tautology A OR NOT A returns everything": "With Sampurn to check",
    "highlight terms are clean tokens": "With the dev - under investigation",
    "excluded term never highlighted": "With the dev - under investigation",
}


def classify(name, passed):
    """Status and note for a check, applying triage."""
    for key, (status, note) in TRIAGED.items():
        if key in name:
            return (status if not passed else "PASS"), note
    if not passed:
        for key, owner in OWNERS.items():
            if key in name:
                return "FAIL", owner
        return "FAIL", ""
    return "PASS", ""
