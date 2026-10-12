"""Fake secrets for the input-guard tests, assembled at run time.

Written out whole, strings like these look like real keys to secret scanners (gitleaks) and fail CI.
Joined from pieces they are not in the source, but the guard still sees the complete shape.
None of them is a real credential.
"""

AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
GITHUB = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
SLACK = "xo" + "xb-" + "1234567890-abcdefghij"
SK_KEY = "s" + "k-" + "abcdefghijklmnopqrstuvwxyz1234"
GOOGLE = "AI" + "za" + "SyA-abcdefghijklmnopqrstuvwxyz012345"
JWT = ".".join(
    ["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0", "abcdefghijklmnop"]
)
URL_PASSWORD = "postgresql" + "://user:" + "hunter2pass" + "@db.example.com/postgres"
API_KEY_LINE = "API_" + "KEY=" + "abcdef123456"
PRIVATE_KEY = "-----BEGIN RSA PRIV" + "ATE KEY-----\nMIIE"
