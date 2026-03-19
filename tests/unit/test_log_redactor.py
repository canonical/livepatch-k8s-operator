# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the log_redactor module."""

import logging
import unittest

from src.log_redactor import RedactingFilter, RedactingFormatter, _redact, setup_log_redaction

_REDACTED = "***REDACTED***"


def _make_record(msg, *args, exc_info=None) -> logging.LogRecord:
    """Create a LogRecord with the given message and args."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=args if args else None,
        exc_info=exc_info,
    )
    return record


class TestRedactFunction(unittest.TestCase):
    """Tests for the _redact() helper function directly."""

    # --- URI pattern ---

    def test_postgresql_uri_credentials_redacted(self):
        """Full postgresql:// URI — user, password, and host are all redacted."""
        result = _redact("postgresql://myuser:s3cr3t@db.host:5432/livepatch")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("s3cr3t", result)
        self.assertNotIn("myuser", result)
        self.assertNotIn("db.host", result)

    def test_postgresql_uri_scheme_preserved(self):
        """The URI scheme (postgresql://) is kept so the log line remains meaningful."""
        result = _redact("postgresql://myuser:s3cr3t@db.host/livepatch")
        self.assertTrue(result.startswith("postgresql://"))

    def test_https_uri_credentials_redacted(self):
        """Credentials embedded in an https:// URI are redacted."""
        result = _redact("https://admin:password123@internal.service/api")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("password123", result)
        self.assertNotIn("admin", result)

    def test_uri_without_credentials_untouched(self):
        """A plain URL with no user-info component is left unchanged."""
        url = "https://contracts.canonical.com/v1/resources"
        self.assertEqual(_redact(url), url)

    def test_uri_partial_in_sentence(self):
        """A URI embedded mid-sentence is still matched and redacted."""
        msg = "connecting to postgresql://user:pass@host/db"
        result = _redact(msg)
        self.assertIn(_REDACTED, result)
        self.assertNotIn("pass", result)

    # --- Auth header pattern ---

    def test_bearer_token_redacted(self):
        """Bearer scheme and token value in an Authorization header are redacted."""
        result = _redact("Authorization: Bearer ghp_abc123xyz")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("ghp_abc123xyz", result)
        self.assertIn("Bearer ", result)

    def test_basic_auth_redacted(self):
        """Basic scheme and base64 credentials in an Authorization header are redacted."""
        result = _redact("Authorization: Basic dXNlcjpwYXNz")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("dXNlcjpwYXNz", result)

    # --- Key=value pattern ---

    def test_password_equals_redacted(self):
        """key=value pair with 'password' key is redacted."""
        result = _redact("password=supersecret")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("supersecret", result)

    def test_password_colon_redacted(self):
        """key: value pair with 'password' key is redacted."""
        result = _redact("password: supersecret")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("supersecret", result)

    def test_secret_quoted_redacted(self):
        """Double-quoted value for a 'secret' key is redacted; quotes are preserved."""
        result = _redact('secret="myvalue"')
        self.assertIn(_REDACTED, result)
        self.assertNotIn("myvalue", result)
        self.assertIn('"', result)

    def test_token_single_quoted_redacted(self):
        """Single-quoted value for a 'token' key is redacted; quotes are preserved."""
        result = _redact("token='abc123'")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("abc123", result)

    def test_api_key_variants_redacted(self):
        """All spelling variants of 'api key' (underscore, hyphen, no separator) are redacted."""
        for key in ("api_key", "api-key", "apikey"):
            with self.subTest(key=key):
                result = _redact(f"{key}=somevalue")
                self.assertIn(_REDACTED, result)
                self.assertNotIn("somevalue", result)

    def test_word_boundary_prevents_false_positive(self):
        """'token_count' must not be redacted — 'token' is not a whole word due to the underscore."""
        result = _redact("token_count=42")
        self.assertEqual(result, "token_count=42")

    def test_non_sensitive_key_untouched(self):
        """Keys that do not match any sensitive pattern are left completely unchanged."""
        msg = "server.url-template=https://livepatch.example.com"
        self.assertEqual(_redact(msg), msg)

    # --- Sensitive env-var pattern ---

    def test_lp_contracts_password_redacted(self):
        """LP_CONTRACTS_PASSWORD env-var assignment has its value redacted."""
        result = _redact("LP_CONTRACTS_PASSWORD=hunter2")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("hunter2", result)
        self.assertIn("LP_CONTRACTS_PASSWORD=", result)

    def test_lp_patch_sync_token_redacted(self):
        """LP_PATCH_SYNC_TOKEN env-var assignment has its value redacted."""
        result = _redact("LP_PATCH_SYNC_TOKEN=tok123")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("tok123", result)

    def test_lp_database_connection_string_redacted(self):
        """LP_DATABASE_CONNECTION_STRING env-var assignment has its value redacted."""
        result = _redact("LP_DATABASE_CONNECTION_STRING=postgresql://u:p@h/db")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("postgresql://u:p@h/db", result)

    def test_lp_s3_secret_key_redacted(self):
        """LP_PATCH_STORAGE_S3_SECRET_KEY env-var assignment has its value redacted."""
        result = _redact("LP_PATCH_STORAGE_S3_SECRET_KEY=secretvalue")
        self.assertIn(_REDACTED, result)
        self.assertNotIn("secretvalue", result)

    def test_innocent_text_untouched(self):
        """A message with no sensitive data is returned unchanged."""
        msg = "workload container not ready - deferring"
        self.assertEqual(_redact(msg), msg)

    def test_mixed_message_only_sensitive_parts_redacted(self):
        """Only the sensitive segment of a mixed message is redacted; safe parts remain."""
        msg = "connecting to endpoint dbhost.example.com:5432 with password=secret"
        result = _redact(msg)
        self.assertIn("dbhost.example.com:5432", result)
        self.assertIn(_REDACTED, result)
        self.assertNotIn("secret", result)


class TestRedactingFilter(unittest.TestCase):
    """Tests for RedactingFilter."""

    def setUp(self):
        self.filter = RedactingFilter()

    def test_filter_always_returns_true(self):
        """The filter must never suppress a record — always returns True."""
        record = _make_record("hello world")
        self.assertTrue(self.filter.filter(record))

    def test_filter_redacts_preformatted_message(self):
        """f-string messages have args=None — filter must handle this."""
        record = _make_record("connecting to postgresql://u:pass@host/db")
        self.filter.filter(record)
        self.assertNotIn("pass", record.msg)
        self.assertIn(_REDACTED, record.msg)

    def test_filter_redacts_percent_style_args(self):
        """%-style positional args that contain sensitive data are redacted in-place."""
        record = _make_record("connecting to %s", "postgresql://user:pass@host/db")
        self.filter.filter(record)
        self.assertNotIn("pass", record.args[0])
        self.assertIn(_REDACTED, record.args[0])

    def test_filter_redacts_dict_style_args(self):
        """Dict-style args (%(key)s) whose values contain sensitive data are redacted."""
        record = _make_record(
            "dsn: %(dsn)s",
            {"dsn": "postgresql://user:topsecret@host/db"},
        )
        self.filter.filter(record)
        self.assertNotIn("topsecret", record.args["dsn"])
        self.assertIn(_REDACTED, record.args["dsn"])

    def test_filter_handles_none_args(self):
        """record.args=None (f-string messages) must not raise exception."""
        record = _make_record("safe message with no args")
        record.args = None
        self.assertTrue(self.filter.filter(record))  # no exception

    def test_filter_handles_empty_tuple_args(self):
        """An empty tuple for record.args must not raise an exception."""
        record = _make_record("message")
        record.args = ()
        self.assertTrue(self.filter.filter(record))  # no exception

    def test_filter_non_string_arg_passed_through(self):
        """Non-string args (int, float) must not be coerced — would break %d/%f specifiers."""
        record = _make_record("exit code: %d", 42)
        self.filter.filter(record)
        self.assertEqual(record.args[0], 42)  # int preserved, not "42"


class TestRedactingFormatter(unittest.TestCase):
    """Tests for RedactingFormatter."""

    def setUp(self):
        self.formatter = RedactingFormatter()

    def test_format_redacts_output(self):
        """Formatter redacts credentials from the final formatted string."""
        record = _make_record("postgresql://user:pass@host/db")
        output = self.formatter.format(record)
        self.assertNotIn("pass", output)
        self.assertIn(_REDACTED, output)

    def test_format_preserves_non_sensitive_output(self):
        """Non-sensitive output is passed through by the formatter unchanged."""
        record = _make_record("workload container not ready")
        output = self.formatter.format(record)
        self.assertIn("workload container not ready", output)

    def test_format_exception_redacts_traceback(self):
        """formatException redacts sensitive data that appears inside exception tracebacks."""
        ei = None
        try:
            raise ValueError("password=s3cr3t in traceback")
        except ValueError:
            import sys

            ei = sys.exc_info()
        result = self.formatter.formatException(ei)
        self.assertNotIn("s3cr3t", result)
        self.assertIn(_REDACTED, result)

    def test_wraps_existing_formatter(self):
        """RedactingFormatter should delegate to the wrapped formatter."""
        inner = logging.Formatter("%(levelname)s - %(message)s")
        formatter = RedactingFormatter(wrapped=inner)
        record = _make_record("connected")
        output = formatter.format(record)
        self.assertIn("INFO", output)
        self.assertIn("connected", output)

    def test_wraps_none_uses_default_formatter(self):
        """Passing wrapped=None falls back to a default Formatter without raising."""
        formatter = RedactingFormatter(wrapped=None)
        record = _make_record("hello")
        # Should not raise
        output = formatter.format(record)
        self.assertIn("hello", output)


class TestSetupLogRedaction(unittest.TestCase):
    """Tests for setup_log_redaction()."""

    def test_wraps_existing_handlers(self):
        """setup_log_redaction wraps each root handler's formatter."""
        root = logging.getLogger()
        handler = logging.StreamHandler()
        original_formatter = logging.Formatter("%(message)s")
        handler.setFormatter(original_formatter)
        root.addHandler(handler)

        try:
            setup_log_redaction()
            self.assertIsInstance(handler.formatter, RedactingFormatter)
        finally:
            root.removeHandler(handler)

    def test_redaction_active_end_to_end(self):
        """A record emitted through the handler must have credentials redacted."""
        import io

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        root = logging.getLogger()
        root.addHandler(handler)

        try:
            setup_log_redaction()
            logger = logging.getLogger("test.e2e")
            logger.warning("connecting to postgresql://user:secret@host/db")
            output = stream.getvalue()
            self.assertNotIn("secret", output)
            self.assertIn(_REDACTED, output)
        finally:
            root.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
