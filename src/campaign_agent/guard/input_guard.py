"""L1 input guard: cheap rules applied to every piece of user text before anything else sees it.

Rejects empty, oversized, control-character, non-English and secret-like input. The text is
normalised (Unicode NFC, trimmed, extra blank lines collapsed) and the cleaned text is what the
model, the evidence check and the stored turn all use. A rejected text is never sent to the model
or to search, and the issue never echoes the secret itself.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

MAX_CHARS = 5000  # the dashboard's campaign_detail limit
MAX_EDIT_CHARS = 2000
MIN_LATIN_SHARE = 0.7  # of all letters; a rough "is this English-like" test, not language detection

Kind = Literal["message", "edit"]

_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "url_with_password": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:@/]+:[^\s@/]+@"),
    "credential_assignment": re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_ -]?key|access[_ -]?token|auth[_ -]?token)\b"
        r"\s*[:=]\s*[^\s,;]{6,}"
    ),
}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BIDI = re.compile("[‪-‮⁦-⁩​-‏﻿]")


@dataclass(frozen=True)
class GuardIssue:
    code: str
    message: str


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str  # the cleaned text; empty when rejected
    issues: tuple[GuardIssue, ...] = ()


def _reject(*issues: GuardIssue) -> GuardResult:
    return GuardResult(False, "", issues)


def _latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return latin / len(letters)


def check_input(text: str, kind: Kind = "message") -> GuardResult:
    limit = MAX_CHARS if kind == "message" else MAX_EDIT_CHARS
    if len(text) > limit * 4:  # do not even normalise something absurd
        return _reject(GuardIssue("too_long", f"longer than {limit} characters"))

    if _CONTROL.search(text) or _BIDI.search(text):
        return _reject(GuardIssue("control_characters", "contains control or invisible characters"))

    cleaned = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return _reject(GuardIssue("empty", "nothing to read"))
    if len(cleaned) > limit:
        return _reject(GuardIssue("too_long", f"longer than {limit} characters"))

    found = [name for name, pattern in _SECRET_PATTERNS.items() if pattern.search(cleaned)]
    if found:
        return _reject(
            *(
                GuardIssue("secret_like", f"looks like a secret ({name}); remove it and try again")
                for name in found
            )
        )
    if _latin_share(cleaned) < MIN_LATIN_SHARE:
        return _reject(GuardIssue("unsupported_language", "only English is supported for now"))
    return GuardResult(True, cleaned)
