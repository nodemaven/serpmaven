"""Results kept on disk for a while, so asking the same search again costs nothing.

A query's records (every page it was asked for) are kept as one JSON file named
by a hash of the query and of the parameters that make it a different search
(language, country, location, filters, pages, links, the proxy's country).
Files are readable by their owner only. A file older than the time to
live is not used and is replaced when the query is asked again. Only queries
answered on every page are kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class ResultCache:
    def __init__(
        self, directory: Path, ttl_seconds: float, params: dict[str, Any], clock=time.time
    ) -> None:
        self.directory = Path(directory)
        self.ttl = ttl_seconds
        self.params = params
        self._clock = clock

    def _file(self, query: str) -> Path:
        key = json.dumps({"q": query.strip(), **self.params}, sort_keys=True, ensure_ascii=False)
        return self.directory / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".json")

    def get(self, query: str) -> list[dict[str, Any]] | None:
        """The records kept for `query`, or None when there are none young enough."""
        try:
            data = json.loads(self._file(query).read_text(encoding="utf-8"))
            if self._clock() - float(data["saved_at"]) > self.ttl:
                return None
            records = data["records"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(records, list) or not records:
            return None
        for record in records:
            if not isinstance(record, dict) or not isinstance(
                record.setdefault("search_metadata", {}), dict
            ):
                return None  # not what this cache writes: ask Google instead
            record["search_metadata"]["cached"] = True
        return records

    def put(self, query: str, records: list[dict[str, Any]]) -> None:
        """Keep `records` for `query`, readable by their owner only (they carry
        the user's queries); a file that cannot be written is skipped."""
        tmp = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = self._file(query)
            fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"saved_at": self._clock(), "records": records}, fh, ensure_ascii=False)
            os.replace(tmp, target)
            tmp = None
        except OSError:
            pass
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
