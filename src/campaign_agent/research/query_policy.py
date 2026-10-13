"""What research may and may not look up. Pure rules, no network.

Research is concept research: what a term means, which product categories use a technology, which industries a
capability maps to. It must never become lead research. So a term or a query is blocked when it:
  * seeks companies ("list of", "companies using", "customers of", "competitors"),
  * seeks people or contact details (email, phone, LinkedIn, an address, a URL),
  * targets sellers instead of customers (the Marketing Agent's supplier words),
  * names an organisation (Inc, GmbH, Ltd ...) or one the campaign excludes,
  * is not a short plain term.
Every block carries a reason that is logged. A blocked need is dropped; the others go on.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Same words as the Marketing Agent's SUPPLIER_WORDS (agent/leadgen/strategy.py): a query that finds sellers.
_SUPPLIER = re.compile(
    r"\b(manufacturers?|suppliers?|distributors?|resellers?|wholesale|dealers?|exporters?|oem|odm|"
    r"makers?|buy|price|prices|for sale|shop|store|catalog(ue)?)\b",
    re.I,
)
_COMPANY_SEEKING = re.compile(
    r"\b(list of|lists of|top \d+|best companies|companies|vendors?|startups?|firms?|"
    r"customers? of|clients? of|who (uses|makes|builds|sells)|competitors?|alternatives? to|"
    r"directory|directories|case stud(y|ies)|clients|customers)\b",
    re.I,
)
_CONTACT = re.compile(
    r"(@|https?://|www\.|\b(e-?mails?|contacts?|phone|telephone|linkedin|twitter|facebook|"
    r"instagram|address|hiring|careers?|jobs?|salary|resume|cv)\b)",
    re.I,
)
_ORGANISATION = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|plc|pvt|s\.a|co\.|ag|oy|ab|bv)\b\.?", re.I
)
_PLAIN_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 +#&./\-]{0,59}$")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


ALLOWED = Decision(True)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", text.lower()).strip()


def check_term(term: str, exclusions: Iterable[str] = ()) -> Decision:
    """Is this term, proposed by the model, safe to build queries from?"""
    term = term.strip()
    if not _PLAIN_TERM.match(term):
        return Decision(False, "not_a_plain_term")
    if m := _CONTACT.search(term):
        return Decision(False, f"contact_or_person_words:{m.group(0).lower()}")
    if m := _SUPPLIER.search(term):
        return Decision(False, f"supplier_words:{m.group(0).lower()}")
    if m := _COMPANY_SEEKING.search(term):
        return Decision(False, f"company_seeking:{m.group(0).lower()}")
    if m := _ORGANISATION.search(term):
        return Decision(False, f"looks_like_an_organisation:{m.group(0).lower()}")
    wanted = _norm(term)
    for excluded in exclusions:
        e = _norm(excluded)
        if e and (e == wanted or f" {e} " in f" {wanted} "):
            return Decision(False, "matches_an_exclusion")
    return ALLOWED


def check_query(query: str, exclusions: Iterable[str] = ()) -> Decision:
    """The last gate before a query reaches the search provider. Applied to every built query."""
    query = query.strip()
    if not query or len(query) > 160:
        return Decision(False, "empty_or_too_long")
    if m := _CONTACT.search(query):
        return Decision(False, f"contact_or_person_words:{m.group(0).lower()}")
    if m := _SUPPLIER.search(query):
        return Decision(False, f"supplier_words:{m.group(0).lower()}")
    if m := _COMPANY_SEEKING.search(query):
        return Decision(False, f"company_seeking:{m.group(0).lower()}")
    if m := _ORGANISATION.search(query):
        return Decision(False, f"looks_like_an_organisation:{m.group(0).lower()}")
    padded = f" {_norm(query)} "
    for excluded in exclusions:
        e = _norm(excluded)
        if e and f" {e} " in padded:
            return Decision(False, "matches_an_exclusion")
    return ALLOWED
