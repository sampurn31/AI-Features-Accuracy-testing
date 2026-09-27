#!/usr/bin/env python3
"""
Self-test for the verdict engine. No network, no credits, no account.

The classification in run_accuracy.py is the part of this harness that decides whether a
response is a defect, and it is also the part a real run cannot prove — an unfunded account
never reaches the model, and a funded run is expensive to repeat. So the cases are exercised
here against synthetic responses, including the ones that matter most: an unchanged document
returned as "Edit applied", and a fabricated value.

    python3 selftest.py

Exits non-zero if any case classifies differently from the expectation.
"""

import sys

import run_accuracy as R


class FakeResponse:
    def __init__(self, status):
        self.status_code = status


class FakeSession:
    def __init__(self, base_html):
        self.base_html = base_html


BASE = "<!DOCTYPE html><html><body><h1>Aarav Deshmukh</h1><p>Pune</p></body></html>"
CHANGED = "<!DOCTYPE html><html><body><h1 style='color:blue'>Aarav Deshmukh</h1><p>Pune</p></body></html>"
WITH_SALARY = BASE.replace("<p>Pune</p>", "<p>Pune</p><p>Current salary: 24,47,500</p>")

CANDIDATE = {
    "currentsalary": 2447500, "contactnumber": "9812345670",
    "lastorganisation": "Meridian Systems", "emailid": "aarav@yopmail.com",
    "city": "Pune", "willingtorelocate": 1, "genderid": 1,
}
OTHER = {
    "currentsalary": 999111, "contactnumber": "9000000001",
    "lastorganisation": "Northwind Ltd", "emailid": "nila@yopmail.com",
}


def envelope(status, message, data=None):
    return {"meta": {"status": status, "message": message}, "data": data or {}}


def run_case(name, scenario, http_status, payload, expected_verdict,
             candidate=None, other=None, base=BASE):
    session = FakeSession(base)
    obs = R.observe(R.Rcrm("ai1"), scenario, FakeResponse(http_status), payload, 1.0,
                    session, candidate or CANDIDATE, other or OTHER)
    verdict, reason = R.classify(scenario, obs)
    ok = verdict == expected_verdict
    print("%-4s %-46s %s" % ("ok" if ok else "FAIL", name, verdict))
    if not ok:
        print("       expected %r" % expected_verdict)
        print("       reason:  %s" % reason)
    return ok


def main():
    results = []

    # -- the headline: unchanged document returned as a plain success -------
    results.append(run_case(
        "no-data no-op returned as 'Edit applied'",
        dict(id="x", category="D.", instruction="i", fixture="sparse", expect="warn_no_data"),
        200,
        envelope(200, "Edit applied", {"html": BASE, "usage": {"no_candidate_data": True},
                                       "credits_consumed": 0.5, "edit_count": 3}),
        R.V_SILENT_NOOP))

    results.append(run_case(
        "no-data no-op WITH the warning returned",
        dict(id="x", category="D.", instruction="i", fixture="sparse", expect="warn_no_data"),
        200,
        envelope(200, R.MSG_NO_DATA_WARNING,
                 {"html": BASE, "usage": {"no_candidate_data": True},
                  "credits_consumed": 0.5}),
        R.V_WARNED))

    results.append(run_case(
        "unchanged with no no-data signal",
        dict(id="x", category="A.", instruction="i", fixture="rich", expect="applied"),
        200,
        envelope(200, "Edit applied", {"html": BASE, "usage": {}, "credits_consumed": 0.5}),
        R.V_UNEXPLAINED_NOOP))

    # -- applied, and whether the value came from the record ---------------
    results.append(run_case(
        "applied with the record's own value present",
        dict(id="x", category="B.", instruction="i", fixture="rich", expect="applied",
             field="currentsalary"),
        200,
        envelope(200, "Edit applied", {"html": WITH_SALARY, "usage": {},
                                       "credits_consumed": 0.5}),
        R.V_APPLIED))

    results.append(run_case(
        "applied but the record's value is absent",
        dict(id="x", category="B.", instruction="i", fixture="rich", expect="applied",
             field="currentsalary"),
        200,
        envelope(200, "Edit applied", {"html": CHANGED, "usage": {},
                                       "credits_consumed": 0.5}),
        R.V_APPLIED_WRONG))

    results.append(run_case(
        "styling applied, no field asserted",
        dict(id="x", category="A.", instruction="i", fixture="rich", expect="applied"),
        200,
        envelope(200, "Edit applied", {"html": CHANGED, "usage": {},
                                       "credits_consumed": 0.5}),
        R.V_APPLIED))

    # -- fabrication --------------------------------------------------------
    results.append(run_case(
        "invented a salary where the record holds none",
        dict(id="x", category="G.", instruction="i", fixture="sparse", expect="either",
             forbid=["per annum", "₹"]),
        200,
        envelope(200, "Edit applied",
                 {"html": BASE.replace("</body>", "<p>₹20,00,000 per annum</p></body>"),
                  "usage": {}, "credits_consumed": 0.5}),
        R.V_FABRICATED))

    # -- the internal marker ------------------------------------------------
    results.append(run_case(
        "no-data marker leaked into the returned html",
        dict(id="x", category="D.", instruction="i", fixture="sparse", expect="warn_no_data"),
        200,
        envelope(200, "Edit applied",
                 {"html": "<!--RCRM_NO_CANDIDATE_DATA-->" + BASE,
                  "usage": {"no_candidate_data": True}, "credits_consumed": 0.5}),
        R.V_MARKER_LEAK))

    # -- refusals -----------------------------------------------------------
    results.append(run_case(
        "refused as expected",
        dict(id="x", category="K.", instruction="i", fixture="rich", expect="refused"),
        200, envelope(422, R.MSG_NOT_RELATED), R.V_REFUSED))

    results.append(run_case(
        "refused when it should have applied",
        dict(id="x", category="B.", instruction="i", fixture="rich", expect="applied"),
        200, envelope(422, R.MSG_NOT_APPLIED), R.V_REFUSED_UNEXPECTED))

    results.append(run_case(
        "applied when it should have been refused",
        dict(id="x", category="K.", instruction="i", fixture="rich", expect="refused"),
        200,
        envelope(200, "Edit applied", {"html": CHANGED, "usage": {}, "credits_consumed": 0.5}),
        R.V_APPLIED_UNEXPECTED))

    results.append(run_case(
        "billed on a refusal",
        dict(id="x", category="K.", instruction="i", fixture="rich", expect="refused"),
        200, envelope(422, R.MSG_NOT_APPLIED, {"credits_consumed": 0.5}),
        R.V_BILLED_ON_REFUSAL))

    results.append(run_case(
        "applied but never billed",
        dict(id="x", category="A.", instruction="i", fixture="rich", expect="applied"),
        200, envelope(200, "Edit applied", {"html": CHANGED, "usage": {}}),
        R.V_UNBILLED_ON_APPLY))

    # -- cross-candidate ----------------------------------------------------
    results.append(run_case(
        "another candidate's value appeared",
        dict(id="x", category="N.", instruction="i", fixture="cross", expect="refused"),
        200,
        envelope(200, "Edit applied",
                 {"html": BASE.replace("</body>", "<p>Northwind Ltd</p></body>"),
                  "usage": {}, "credits_consumed": 0.5}),
        R.V_CROSS_CANDIDATE_LEAK))

    # -- value matching, the part that produces false defects if it is wrong -
    print()
    checks = [
        ("reformatted number 2447500 -> 24,47,500", "<p>24,47,500</p>", "2447500", True),
        ("reformatted number with spaces", "<p>2 447 500</p>", "2447500", True),
        ("a different number does not match", "<p>1234567</p>", "2447500", False),
        ("case-insensitive text", "<p>MERIDIAN SYSTEMS</p>", "Meridian Systems", True),
        ("text broken across whitespace", "<p>Meridian\n   Systems</p>", "Meridian Systems", True),
        ("absent text", "<p>Northwind</p>", "Meridian Systems", False),
        ("short numbers are not digit-matched", "<p>4511</p>", "45", False),
    ]
    for name, html, token, expected in checks:
        got = R.value_present(html, token)
        ok = got == expected
        results.append(ok)
        print("%-4s value_present: %-44s %s" % ("ok" if ok else "FAIL", name, got))

    print()
    failed = results.count(False)
    print("%d cases, %d failed" % (len(results), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
