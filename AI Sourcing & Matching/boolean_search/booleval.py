"""
A boolean expression parser and evaluator, used as the oracle for record-level checks.

The product hands the recruiter's boolean query to OpenSearch as a `query_string`, so
the meaning of AND / OR / NOT / parentheses / quoted phrases is Lucene's. This module
implements the *textbook* reading of the same expression — AND binds tighter than OR,
parentheses override, NOT applies to the term or group that follows it — and evaluates
it against a candidate's text.

The point is not to reimplement Lucene. It is to have an independent answer to "should
this candidate have come back?", so that a returned record can be checked rather than
assumed. Where the two disagree, the disagreement is the finding, and the report says
which side each disagreement favours:

  * an EXCLUDED term present in a returned record is conclusive — no reading of
    `NOT x` admits a record containing x;
  * an INCLUDED term missing is a lead, because the analyser may have matched a stem,
    a synonym, or a field this harness cannot see.
"""
import re

AND, OR, NOT, LPAREN, RPAREN, TERM = "AND", "OR", "NOT", "(", ")", "TERM"


def tokenize(expr):
    """Split into operators, parentheses, quoted phrases and bare terms."""
    tokens, i, n = [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            tokens.append((LPAREN if ch == "(" else RPAREN, ch))
            i += 1
            continue
        if ch == '"':
            j = expr.find('"', i + 1)
            if j == -1:  # unbalanced quote: take the rest as one phrase
                tokens.append((TERM, expr[i + 1:].strip()))
                break
            tokens.append((TERM, expr[i + 1:j]))
            i = j + 1
            continue
        j = i
        while j < n and not expr[j].isspace() and expr[j] not in '()"':
            j += 1
        word = expr[i:j]
        upper = word.upper()
        if upper in (AND, OR, NOT):
            tokens.append((upper, word))
        else:
            tokens.append((TERM, word))
        i = j
    return tokens


class Parser:
    """Recursive descent: expression := or_expr; OR is loosest, NOT is tightest."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def eat(self):
        token = self.peek()
        self.pos += 1
        return token

    def parse(self):
        node = self.parse_or()
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek()[0] == OR:
            self.eat()
            right = self.parse_and()
            node = ("or", node, right)
        return node

    def parse_and(self):
        node = self.parse_unary()
        while True:
            kind = self.peek()[0]
            if kind == AND:
                self.eat()
                node = ("and", node, self.parse_unary())
            elif kind in (TERM, LPAREN, NOT):
                # Adjacent terms with no operator: Lucene's default is OR, and the
                # product sets no default_operator, so adjacency is treated as OR here.
                node = ("or", node, self.parse_unary())
            else:
                return node

    def parse_unary(self):
        kind, value = self.peek()
        if kind == NOT:
            self.eat()
            return ("not", self.parse_unary())
        if kind == LPAREN:
            self.eat()
            node = self.parse_or()
            if self.peek()[0] == RPAREN:
                self.eat()
            return node
        if kind == TERM:
            self.eat()
            return ("term", value)
        self.eat()  # stray token
        return ("true",)


def parse(expr):
    return Parser(tokenize(expr)).parse()


def evaluate(node, contains):
    """Evaluate a parsed tree. `contains(term) -> bool` supplies the text lookup."""
    kind = node[0]
    if kind == "term":
        return contains(node[1])
    if kind == "not":
        return not evaluate(node[1], contains)
    if kind == "and":
        return evaluate(node[1], contains) and evaluate(node[2], contains)
    if kind == "or":
        return evaluate(node[1], contains) or evaluate(node[2], contains)
    return True


def terms(node, negated=False, out=None):
    """Every term in the expression, tagged with whether it sits under a NOT."""
    if out is None:
        out = []
    kind = node[0]
    if kind == "term":
        out.append((node[1], negated))
    elif kind == "not":
        terms(node[1], not negated, out)
    elif kind in ("and", "or"):
        terms(node[1], negated, out)
        terms(node[2], negated, out)
    return out


def excluded_terms(expr):
    """
    Terms that must not appear in any returned record.

    Only terms negated an odd number of times and not sitting under an OR with a
    positive sibling — a term under `A OR NOT B` is not globally excluded, since a
    record can satisfy the expression through A while still containing B. Restricting
    to the unambiguous case keeps the conclusive check conclusive.
    """
    node = parse(expr)
    if _has_top_level_or(node):
        return []
    return [term for term, negated in terms(node) if negated]


def _has_top_level_or(node):
    if node[0] == "or":
        return True
    if node[0] == "and":
        return _has_top_level_or(node[1]) or _has_top_level_or(node[2])
    return False


def satisfies(expr, text, contains_fn):
    return evaluate(parse(expr), lambda t: contains_fn(text, t))
