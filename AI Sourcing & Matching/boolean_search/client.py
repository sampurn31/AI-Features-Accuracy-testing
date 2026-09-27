"""
Shared client for the AI Sourcing V3 boolean-search harness.

Talks to two services:
  * Albatross, for sign-in only.
  * neptune, for `/ai-sourcing-v3/search` and `/ai-sourcing-v3/search/count`.

`/search` answers with newline-delimited JSON chunks rather than one document: a
`metadata` chunk first, then one `candidate` chunk per record. The metadata carries
`queryUsed` (the parsed filter params) and `openSearchQuery` (the DSL actually executed),
which is what lets this harness check the generated query as well as the results it
produced.

The reader below tolerates a truncated stream. A long response intermittently ends in
`IncompleteRead`, and dropping the whole run on that would lose the chunks already
delivered — every complete line before the break is still valid and is kept.
"""
import json
import os
import re
import ssl
import urllib.error
import urllib.request


class SearchError(Exception):
    """A non-200 from the service. Carried, not raised past the check that caused it:
    a 500 on a particular query shape is a finding about that shape, and aborting the
    run would lose every relation that had not been measured yet."""

    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body

DEFAULT_ENV = os.environ.get("AIS_ENV", "ai4")


def albatross_url(env=DEFAULT_ENV):
    return f"https://albatross-{env}.recruitcrm.net/v1"


def neptune_url(env=DEFAULT_ENV):
    return f"https://{env}-neptune.recruitcrm.net/v1"


class SearchClient:

    def __init__(self, email=None, password=None, env=DEFAULT_ENV):
        self.env = env
        self.email = email or os.environ.get("AIS_EMAIL")
        self.password = password or os.environ.get("AIS_PASSWORD")
        if not self.email or not self.password:
            raise SystemExit(
                "Set AIS_EMAIL and AIS_PASSWORD (or pass them in). Credentials are never "
                "stored in this harness.")
        self._token = None
        self._ctx = ssl.create_default_context()

    # -- transport ---------------------------------------------------------------

    def _post(self, url, body, timeout=240, auth=True):
        headers = {"content-type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as response:
                buf = b""
                while True:
                    try:
                        chunk = response.read(65536)
                    except Exception:
                        # Truncated stream: keep everything already received.
                        break
                    if not chunk:
                        break
                    buf += chunk
        except urllib.error.HTTPError as exc:
            raise SearchError(exc.code, exc.read().decode("utf-8", "replace")) from None
        return buf.decode("utf-8", "replace")

    @property
    def token(self):
        if self._token is None:
            raw = self._post(albatross_url(self.env) + "/login",
                             {"email": self.email, "password": self.password},
                             timeout=60, auth=False)
            self._token = json.loads(raw)["data"]["token"]
        return self._token

    # -- endpoints ---------------------------------------------------------------

    @staticmethod
    def keyword_body(boolean_query, **extra):
        keyword_search = {"keywords": {"booleanQuery": boolean_query}}
        keyword_search.update(extra)
        return {"keywordSearch": keyword_search}

    def count(self, boolean_query, **extra):
        """Result count for a query. One second a call, so this is the bulk instrument."""
        raw = self._post(neptune_url(self.env) + "/ai-sourcing-v3/search/count",
                         self.keyword_body(boolean_query, **extra), timeout=120)
        return json.loads(raw)["count"]

    def search(self, boolean_query, **extra):
        """
        Full page-1 search. Returns (metadata, [candidate records]).

        Ten to fifteen seconds a call, so the record-level checks run over a smaller set
        than the count-level algebra.
        """
        raw = self._post(neptune_url(self.env) + "/ai-sourcing-v3/search",
                         self.keyword_body(boolean_query, **extra), timeout=300)
        metadata, candidates = {}, []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line).get("data")
            except json.JSONDecodeError:
                continue  # a half-written final line on a truncated stream
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "metadata":
                metadata = payload
            elif payload.get("type") == "candidate":
                inner = payload.get("candidate")
                if isinstance(inner, dict):
                    candidates.append(inner)
        return metadata, candidates


# -- candidate text ---------------------------------------------------------------

# The index fields the boolean query runs against, per the generated DSL:
#   summary^3.0, headline^2.0, experience.description^1.5, experience.title^1.0,
#   experience.company_name^1.0
# The API returns a presentation shape rather than the raw document, so these are the
# returned fields that carry the same content. `description` is the returned name for
# `summary`, and `position` for `headline`.
SEARCHED_TEXT_FIELDS = ("description", "position", "company")

# keySkills is not one of the queried fields, but it is included when reporting a
# suspected miss: a candidate whose only mention of the term is in keySkills explains an
# apparent violation as an indexing question rather than a boolean-logic one.
CONTEXT_FIELDS = ("keySkills",)


def candidate_text(candidate, include_context=False):
    """The text a boolean term could legitimately have matched on."""
    parts = []
    for field in SEARCHED_TEXT_FIELDS:
        value = candidate.get(field)
        if isinstance(value, str):
            parts.append(value)
    for entry in candidate.get("experience") or []:
        if isinstance(entry, dict):
            for key in ("description", "title", "company_name", "company"):
                value = entry.get(key)
                if isinstance(value, str):
                    parts.append(value)
    if include_context:
        for field in CONTEXT_FIELDS:
            value = candidate.get(field)
            if isinstance(value, list):
                parts.extend(str(v) for v in value)
            elif isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def contains_term(text, term):
    """
    Whether a term appears in the text, on word boundaries and case-insensitively.

    Deliberately literal. The index analyses text (lower-casing, stemming, splitting on
    punctuation), so this is a close but not exact model of what OpenSearch matched:
    it can report a miss where the analyser matched a stem. That asymmetry is why the
    two directions are weighted differently in the report — an excluded term found in a
    returned record is conclusive, a missing included term is a lead.
    """
    if not term:
        return False
    term = term.strip().strip('"')
    if not term:
        return False
    if " " in term:  # quoted phrase
        return re.search(re.escape(term), text, re.IGNORECASE) is not None
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
                     text, re.IGNORECASE) is not None
