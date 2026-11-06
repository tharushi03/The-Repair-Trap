"""Run with: python -m unittest discover tests"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sanitize import sanitize  # noqa: E402

# Fake credentials are assembled at runtime so secret scanners ignore this file.
GH = "ghp_" + "a1B2c3D4e5" * 4
SK = "sk-" + "proj" + "x9Y8z7W6v5" * 3
AWS = "AKIA" + "ABCDEFGH23456789"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTYifQ" + ".abcDEF123_-xyz"
BLOB = "dGhpcyBpcyBhIGxvbmcgYmFzZTY0IGJsb2IgdGhhdCBzaG91bGQgZ28gYXdheQ=="


class Redacts(unittest.TestCase):
    def assert_gone(self, secret, line):
        output = sanitize(line)
        self.assertNotIn(secret, output)
        self.assertIn("[REDACTED", output)

    def test_vendor_tokens(self):
        for secret in (GH, SK, AWS, JWT):
            self.assert_gone(secret, f"using {secret} now")

    def test_private_key_block(self):
        body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
        key = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"
        self.assert_gone(body, key)

    def test_truncated_private_key_block(self):
        self.assert_gone("MIIEvQIBAD", "-----BEGIN PRIVATE KEY-----\nMIIEvQIBAD\nlog ends")

    def test_database_url(self):
        output = sanitize("postgresql://admin:hunter22@db.internal:5432/app")
        self.assertNotIn("hunter22", output)
        self.assertIn("db.internal", output)  # credentials gone, host stays as context

    def test_assignments(self):
        for line in ("DB_PASSWORD=hunter22", 'password: "hunter22"', "API_KEY = hunter22",
                     "export JWT_SECRET=hunter22", "CLIENT_SECRET: hunter22"):
            self.assert_gone("hunter22", line)

    def test_secret_with_punctuation_does_not_leak_its_tail(self):
        for line in ("password=abc]tail", "password=abc,tail", "token=abc;tail", "secret=abc)tail"):
            self.assert_gone("tail", line)

    def test_database_url_assignment_without_password(self):
        self.assert_gone("db.internal", "DATABASE_URL=postgres://db.internal/app")

    def test_auth_header(self):
        self.assert_gone("abcdef123456789", "Authorization: Bearer abcdef123456789")

    def test_base64_blob(self):
        self.assert_gone(BLOB, f"payload {BLOB} end")

    def test_long_hex_secret(self):
        secret = "9f" * 32
        self.assert_gone(secret, f"value {secret}")

    def test_email_and_ip(self):
        self.assert_gone("someone@example.org", "author someone@example.org")
        self.assert_gone("10.0.3.17", "connect to 10.0.3.17 failed")

    def test_denylist(self):
        output = sanitize("customer Acme Corp failed", re.compile("acme corp", re.I))
        self.assertNotIn("Acme", output)


class Preserves(unittest.TestCase):
    """False positives destroy the error text that the corpus is for."""

    def assert_kept(self, line):
        self.assertEqual(sanitize(line), line)

    def test_error_context(self):
        for line in (
            "Error: P3006 - Migration `20260101_orders` failed to apply cleanly",
            "/home/runner/work/example-app/example-app/apps/backend/src/modules/orders/x.ts:12",
            "PROJ-18-orders-database-update-with-new-vendor-and-stock-table-layout",
            "TS2322: Type 'number' is not assignable to parameter password: string",
            "token: ***",
            "password: undefined",
            "password: string,",
            "function login(token: string) {",
            "API_TOKEN: ${{ secrets.API_TOKEN }}",
            "digest sha256:" + "ab12" * 16,
            "commit 0123456789abcdef0123456789abcdef01234567",
            "127.0.0.1:5432 connection refused",
        ):
            self.assert_kept(line)

    def test_strips_ansi(self):
        self.assertEqual(sanitize("\x1b[31mError\x1b[0m"), "Error")

    def test_idempotent(self):
        once = sanitize(f"{GH} DB_PASSWORD=x {BLOB} a@b.co 10.1.2.3")
        self.assertEqual(sanitize(once), once)


if __name__ == "__main__":
    unittest.main()
