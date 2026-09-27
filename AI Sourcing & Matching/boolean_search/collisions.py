"""
Layer 5 — terms that CONTAIN an operator word.

The single most dangerous thing a hand-rolled boolean tokenizer does is match its
operators as substrings rather than as whole words. The parser this feature used to run
did exactly that: it tested `expr[i:i+4].upper().startswith('AND ')` at every character,
so `stand up` tokenised as `st` AND `up`, and `Android developer` as `` AND
`roid developer`. Both are ordinary recruiting vocabulary, and neither produces an error
— just a silently different search.

So every term here carries `and`, `or` or `not` inside it, at the start, in the middle or
at the end, and all of them are words a recruiter actually types: Android, Oracle,
WordPress, Salesforce, Senior, Coordinator, Notion, Standard, Poland, Thailand.

Three things are asserted per term:
  1. it answers at all (a split term can produce a 500 on the fragment);
  2. `queryUsed.keywords` is exactly the term, not fragments of it;
  3. `highlightTerms` is exactly the term — the highlight list is built from its own parse,
     so it regresses independently of the search itself.

Then the composite cases, where a collision term sits beside real operators — including
the one that prompted this layer, `Retail OR stand OR pop up`, which is checked against
three-set inclusion-exclusion so the count itself is verified and not just the parse.

Finally the mirror case: an operator word appearing as a genuine word inside a quoted
phrase (`"research and development"`) must be literal text, not an operator.
"""

# (label, term, which operator word it contains, where)
COLLISION_TERMS = [
    ("Android", "Android", "and", "start"),
    ("Andover", "Andover", "and", "start"),
    ("stand", "stand", "and", "middle"),
    ("Standard", "Standard", "and", "middle"),
    ("Sandbox", "Sandbox", "and", "middle"),
    ("brand", "brand", "and", "end"),
    ("demand", "demand", "and", "end"),
    ("Poland", "Poland", "and", "end"),
    ("Thailand", "Thailand", "and", "end"),
    ("candidate", "candidate", "and", "middle"),
    ("Alexander", "Alexander", "and", "middle"),
    ("understanding", "understanding", "and", "middle"),
    ("Oracle", "Oracle", "or", "start"),
    ("Order", "Order", "or", "start"),
    ("Organisation", "Organisation", "or", "start"),
    ("WordPress", "WordPress", "or", "middle"),
    ("Storage", "Storage", "or", "middle"),
    ("Salesforce", "Salesforce", "or", "middle"),
    ("Coordinator", "Coordinator", "or", "middle"),
    ("Senior", "Senior", "or", "end"),
    ("Junior", "Junior", "or", "end"),
    ("Mentor", "Mentor", "or", "end"),
    ("Notion", "Notion", "not", "start"),
    ("Notebook", "Notebook", "not", "start"),
    ("Nottingham", "Nottingham", "not", "start"),
    ("Annotation", "Annotation", "not", "middle"),
    ("cannot", "cannot", "not", "end"),
    ("wahi", "wahi", "-", "none"),
]

# Terms whose highlight list is checked too. /search costs ~12s, so this is the subset
# that spans the three operators and all three positions.
HIGHLIGHT_SUBSET = ["Android", "stand", "Poland", "Oracle", "WordPress", "Senior",
                    "Notion", "Annotation", "cannot", "wahi"]

# The composite that prompted this layer, plus siblings mixing collisions with operators.
COMPOSITES = [
    ("the reported case", "Retail OR stand OR pop up", ["Retail", "stand", "pop up"]),
    ("collision AND collision", "Android AND Oracle", ["Android", "Oracle"]),
    ("collision OR collision", "Android OR Notion", ["Android", "Notion"]),
    ("collision with exclusion", "Android AND NOT Notion", ["Android", "Notion"]),
    ("three collisions OR-ed", "Standard OR Senior OR Notebook",
     ["Standard", "Senior", "Notebook"]),
    ("collision inside a group", "(Android OR Oracle) AND Senior",
     ["Android", "Oracle", "Senior"]),
    ("collision excluded in a group", "Senior AND NOT (Android OR Notion)",
     ["Senior", "Android", "Notion"]),
    ("multi-word term unquoted", "pop up", ["pop up"]),
]

# The set whose union is verified by inclusion-exclusion — the user's exact case.
UNION_TERMS = ["Retail", "stand", "pop up"]

# An operator word used as a real word. Quoted, it must stay literal; unquoted, the
# parser is entitled to read it as an operator — the pair is what makes that visible.
PHRASE_CASES = [
    ("quoted phrase containing 'and'", '"research and development"'),
    ("quoted phrase containing 'or'", '"search or select"'),
    ("quoted phrase containing 'not'", '"not for profit"'),
    ("quoted multi-word collision", '"stand up"'),
]
