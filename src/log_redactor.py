# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Log redaction module.

Provides a logging Filter and Formatter that scrub sensitive information
(passwords, tokens, API keys, connection strings) from log records before
they reach any handler.

To activate redaction, call ``setup_log_redaction()`` once early in the
charm's ``__init__`` method, after ``super().__init__()``.

NOTE: If new sensitive config keys are added to config.yaml, add the
corresponding ``LP_*`` env-var name to ``_SENSITIVE_ENV_VAR_PATTERN``.
"""

import logging
import re
from typing import Optional

_REDACTED = "***REDACTED***"

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# URIs with embedded credentials: scheme://user:password@host/...
# Replaces everything after the scheme (userinfo + host + path).
_URI_PATTERN = re.compile(
    r"(?P<scheme>\w[\w+\-.]*://)"  # e.g. postgresql://
    r"[^:@\s]+:[^@\s]+"  # user:password  (redacted)
    r"@\S*",  # @host/db...    (redacted)
    re.IGNORECASE,
)

# HTTP Authorization header values: Bearer/Basic <credential>
_AUTH_HEADER_PATTERN = re.compile(
    r"(?P<scheme>(?:Bearer|Basic)\s+)\S+",
    re.IGNORECASE,
)

# Generic key=value / key: value pairs where the key name implies a secret.
# Uses \b word boundaries to avoid false positives (e.g. "token_count=5").
_KV_PATTERN = re.compile(
    r"(?P<key>\b(?:"
    r"password|passwd|secret|token|"
    r"api[_\-]?key|api[_\-]?secret|"
    r"access[_\-]?key|private[_\-]?key|auth[_\-]?key|"
    r"credentials|host|user|username"
    r")\b)"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^\s'\"]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)

# Specific charm env-var assignments that carry sensitive config values.
# Covers env vars produced by map_config_to_env_vars() and explicit sets in
# charm.py get_env_vars().
_SENSITIVE_ENV_VAR_PATTERN = re.compile(
    r"(?P<varname>"
    r"LP_CONTRACTS_PASSWORD"
    r"|LP_PATCH_SYNC_TOKEN"
    r"|LP_PATCH_STORAGE_S3_SECRET_KEY"
    r"|LP_PATCH_STORAGE_S3_ACCESS_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_API_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_USERNAME"
    r"|LP_AUTH_BASIC_USERS"
    r"|LP_AUTH_SSO_PUBLIC_KEY"
    r"|LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING"
    r"|LP_DATABASE_CONNECTION_STRING"
    r")"
    r"(?P<sep>=)"
    r"(?P<value>\S+)",
    re.IGNORECASE,
)

_PATTERNS = [
    (
        _URI_PATTERN,
        lambda m: f"{m.group('scheme')}{_REDACTED}",
    ),
    (
        _AUTH_HEADER_PATTERN,
        lambda m: f"{m.group('scheme')}{_REDACTED}",
    ),
    (
        _KV_PATTERN,
        lambda m: (f"{m.group('key')}{m.group('sep')}" f"{m.group('quote')}{_REDACTED}{m.group('quote')}"),
    ),
    (
        _SENSITIVE_ENV_VAR_PATTERN,
        lambda m: f"{m.group('varname')}{m.group('sep')}{_REDACTED}",
    ),
]


def _redact(msg: str) -> str:
    """Apply all redaction patterns to *msg* and return the sanitised result."""
    for pattern, replacement in _PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg


def _redact_if_str(value: object) -> object:
    """Redact *value* if it is a string; return it unchanged otherwise."""
    return _redact(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class RedactingFilter(logging.Filter):
    """Logging filter that redacts sensitive data from log records in-place.

    Handles both pre formatted log messages (record.msg) and structured log arguments.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitise *record* in-place; never suppresses the record."""
        record.msg = _redact(str(record.msg))
        if isinstance(record.args, dict):
            record.args = {k: _redact_if_str(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_redact_if_str(arg) for arg in record.args)
        return True


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class RedactingFormatter(logging.Formatter):
    """Formatter that delegates to a wrapped formatter then redacts the output.

    Acts as a last line of defence: catches sensitive data not intercepted by
    the filter — for example, values embedded in exception tracebacks or
    messages from loggers that bypass the root-logger filter chain.
    """

    def __init__(self, wrapped: Optional[logging.Formatter] = None) -> None:
        """Init function. If *wrapped* is None, uses a default logging.Formatter."""
        super().__init__()
        self._wrapped = wrapped if wrapped is not None else logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        """Override format to redact the formatted log message."""
        return _redact(self._wrapped.format(record))

    def formatException(self, ei) -> str:  # noqa: N802
        """Override formatException to redact the formatted exception."""
        return _redact(self._wrapped.formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802
        """Override formatStack to redact the formatted stack info."""
        return _redact(self._wrapped.formatStack(stack_info))


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------


def setup_log_redaction() -> None:
    """Attach redaction formatter to the root logger.

    Must be called after ops has initialised the root logger — i.e. after
    ``super().__init__()`` inside the charm's ``__init__`` method.

    Effects:
    - Each existing handler on the root logger has its formatter wrapped with
      ``RedactingFormatter`` as a safety net.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(RedactingFormatter(handler.formatter))
