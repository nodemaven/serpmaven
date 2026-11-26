"""How an exception is described in records and events."""

from __future__ import annotations

import re

_CREDENTIALS = re.compile(r"//[^/\s:@]+:[^@\s]+@")


def describe_error(exc: BaseException) -> str:
    """One line naming an exception, for records and events.

    Browser errors can be long and can quote the proxy URL they were given, so
    only the first line is kept, it is truncated, and `user:pass@` is masked.
    """
    lines = str(exc).strip().splitlines()
    first = _CREDENTIALS.sub("//***@", lines[0] if lines else "")[:200]
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__
