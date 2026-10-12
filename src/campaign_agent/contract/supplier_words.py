"""Words that turn a customer search into a supplier search.

Copied from the Marketing Agent (agent/leadgen/strategy.py, SUPPLIER_WORDS) at the commit
recorded in model.BASE_CONFIG_SOURCE. The MA raises RequirementDriftError, in the middle of a
run, for any query that contains one, so a campaign value with one of these words must never
reach it. Keep in step with the MA.
"""

import re

SUPPLIER_WORDS = re.compile(
    r"\b(manufacturers?|suppliers?|distributors?|resellers?|wholesale|dealers?|exporters?|oem|odm|"
    r"makers?|buy|price|prices|for sale|shop|store|catalog(ue)?)\b",
    re.IGNORECASE,
)


def supplier_word(text: str) -> str | None:
    """The first supplier word in the text, or None."""
    match = SUPPLIER_WORDS.search(text)
    return match.group(0) if match else None
