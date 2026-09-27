#!/usr/bin/env python3
"""
Is a refused AI edit actually billed? Measured from the BALANCE, not the response body.

Why this exists: the /edit rejection path returns `respond({}, ...)` — an empty data object —
so `credits_consumed` is absent from *every* 422 regardless of whether a charge happened.
Reading "not billed" off that absence is unsound, and it is the mistake this script corrects.
The account balance is the only ground truth, so each case is measured by reading the balance
before and after, exactly as the tester did by hand in the UI.

The edit counter has the same problem: `edit_count` is only present on a 200. So whether a
refusal consumes one of the session's 15 edits is established by BRACKETING it between two
applied edits and comparing their counters — a delta of 1 means the refusal was free, 2 means
it counted.

    python3 verify_billing.py --email you@example.com --password secret

Each case costs two control edits plus the case itself, about 1.5 credits.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_accuracy import (Rcrm, EditSession, PATH_SUBSCRIPTION, STANDARD_FIELD_SELECTION,
                          rich_candidate_fields, custom_field_values, log)

# Cycled, so the number of cases is not limited by the length of this list.
import itertools
CONTROLS = itertools.cycle([
    "Make the header text blue",
    "Change all section headings to uppercase",
    "Centre align the entire header block",
    "Increase the line height in all paragraphs to 1.6",
    "Give the Skills section a light grey background",
    "Make the candidate name bold and 28px",
    "Reduce the header padding to 12px",
    "Add 24px of spacing between every section",
    "Make the section titles dark blue",
    "Set the header background colour to #eef3f8",
])


def free_credit(api):
    """`data.free_credit` — the column the tester watched in the UI."""
    r = api.get(api.albatross, PATH_SUBSCRIPTION)
    d = api.data_obj(api.body(r))
    return float(d.get("free_credit") or 0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="ai1")
    p.add_argument("--email", default=os.environ.get("RCRM_EMAIL", ""))
    p.add_argument("--password", default=os.environ.get("RCRM_PASSWORD", ""))
    args = p.parse_args()
    email = args.email or input("Account email: ").strip()
    password = args.password or getpass.getpass("Password: ")

    api = Rcrm(args.env)
    api.login(email, password)
    log("free_credit at start: %.2f" % free_credit(api))

    cv_field, _ = api.ensure_custom_field("ACC_RFA_CV_FILE", "file")
    text_slots = api.reusable_custom_fields("text", 5)
    custom_payload, _ = custom_field_values(text_slots)
    st, msg, _ = api.save_settings(cv_field, STANDARD_FIELD_SELECTION[:9],
                                   text_slots[:1], "Format the resume cleanly.")
    if st != 200:
        raise SystemExit("settings save failed: %s" % msg)

    cand = api.create_candidate(rich_candidate_fields("bill" + os.urandom(2).hex(),
                                                      custom_payload))
    log("candidate %s" % cand["slug"])
    session = EditSession(api, cand["slug"], "rich")
    log("session ready, base document %d chars\n" % len(session.base_html))

    # The email is in the generated header already, so asking for it again is the
    # tester's "detail already exists in this section" case.
    # Three trials of each refusal path, because one sample cannot contradict a UI
    # observation — an LLM-backed endpoint takes a different path run to run.
    cases = []
    for trial in (1, 2, 3):
        cases += [
            ("already in header t%d" % trial,
             "Add the candidate's email address to the header"),
            ("same section, explicit repeat t%d" % trial,
             "Add the candidate's email address to the header, it should appear there twice"),
            ("update phrasing t%d" % trial,
             "Correct the email address in the resume to the one on the record"),
            ("relevance rejection t%d" % trial,
             "What is the capital of France right now"),
            ("ungrounded, no record holds it t%d" % trial,
             "Add the candidate's driving licence number to the footer"),
        ]

    controls = CONTROLS
    rows = []
    for label, instruction in cases:
        # Every call goes through EditSession.run, which resets the session with
        # new_session=True before the 15-edit cap is reached. Calling api.edit directly
        # exhausted the session on the second trial and turned every later row into a 429.
        r, payload, _, _ = session.run(next(controls))
        d1 = api.data_obj(payload)
        b1, c1, t1 = free_credit(api), d1.get("edit_count"), d1.get("remaining_tokens")

        r, payload, _, _ = session.run(instruction)
        status = api.meta_status(payload, r.status_code)
        message = api.meta_message(payload)
        data = api.data_obj(payload)
        reported = data.get("credits_consumed")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        b2 = free_credit(api)

        r, payload, _, _ = session.run(next(controls))
        d2 = api.data_obj(payload)
        c2, t2 = d2.get("edit_count"), d2.get("remaining_tokens")

        charged = round(b1 - b2, 4)
        # Tokens are the other thing a request can consume, and the relevance judge makes
        # its own model call. Bracketing two applied edits shows what the middle request
        # took: a drop larger than one edit's worth means the refusal cost tokens.
        tokens = (t1 - t2) if isinstance(t1, int) and isinstance(t2, int) else None
        counted = ("unknown" if not (isinstance(c1, int) and isinstance(c2, int))
                   else "YES" if c2 - c1 == 2 else "no" if c2 - c1 == 1
                   else "reset/delta %s" % (c2 - c1))
        rows.append((label, instruction, status, message, reported, charged, counted,
                     bool(usage.get("no_candidate_data")), tokens))
        log("%-36s status=%-4s credits %+0.2f  tokens=%-9s counted=%-8s reported=%s"
            % (label, status, -charged, tokens, counted, reported))
        log("   %s" % message[:96])

    print("\n" + "=" * 108)
    print("%-36s %-6s %-9s %-10s %-11s %-8s %s" %
          ("case", "status", "reported", "CREDITS", "TOKENS", "counted", "ungrounded"))
    print("-" * 108)
    for label, instr, status, msg, reported, charged, counted, ungrounded, tokens in rows:
        print("%-36s %-6s %-9s %-10s %-11s %-8s %s" %
              (label[:36], status, reported, "-%.2f" % charged,
               ("-%d" % tokens) if isinstance(tokens, int) else "n/a", counted, ungrounded))
    print("=" * 108)
    print("\n'reported' is data.credits_consumed from the response body; 'ACTUALLY' is the")
    print("measured change in data.free_credit. Where those disagree, the body is wrong and")
    print("the balance is right.")
    log("free_credit at end: %.2f" % free_credit(api))


if __name__ == "__main__":
    main()
