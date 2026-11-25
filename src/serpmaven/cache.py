"""Results kept on disk for a while, so asking the same search again costs nothing.

A query's records (every page it was asked for) are kept as one JSON file named
by a hash of the query and of the parameters that make it a different search
(language, country, location, filters, pages). A file older than the time to
live is not used and is replaced when the query is asked again. Only queries
answered on every page are kept.
"""

from __future__ import annotations

import hashlib
import json
import os
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
            record.setdefault("search_metadata", {})["cached"] = True
        return records

    def put(self, query: str, records: list[dict[str, Any]]) -> None:
        """Keep `records` for `query`; a file that cannot be written is skipped."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self._file(query)
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps({"saved_at": self._clock(), "records": records}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, target)
        except OSError:
            pass
