from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolutionKey:
    event_bp_id: str
    regime: str  # "prematch" | "live"

    def as_str(self) -> str:
        return f"{self.event_bp_id}:{self.regime}"

    @classmethod
    def from_str(cls, raw: str) -> "ResolutionKey":
        event, regime = raw.split(":", 1)
        return cls(event, regime)


class ResolutionCache:
    """Get-or-resolve cache for cross-bookmaker ids, persisted to JSON.

    Each new entry is written to disk immediately so a hard kill never
    loses cached ids.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._loaded = False

    def load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("resolution cache unreadable, starting fresh: %s", e)
                self._data = {}
        self._loaded = True
        self._scrub_poisoned_b9j_prematch()

    def _scrub_poisoned_b9j_prematch(self) -> None:
        """Drop cached entries that have an sr_id but no b9j_id.

        Two sources of this poisoning:
          - prematch: the bet9ja prematch map takes ~2 min to build, so
            events resolved during startup get b9j_id=None cached forever.
          - live: find_event_id_by_sr_id can transiently miss an event
            mid-match; that None would also stick.

        b9j_id == genius_id (for live) is NOT poisoning anymore — the
        resolver intentionally falls back to genius_id as a candidate
        EVENTID when sr-lookup fails. That equality is now a legitimate
        cache state, not a stale-code marker.
        """
        poisoned = [
            k for k, v in self._data.items()
            if v.get("sr_id") and not v.get("b9j_id")
        ]
        if not poisoned:
            return
        for k in poisoned:
            del self._data[k]
        log.info(
            "resolution cache: dropped %d poisoned entries "
            "(sr_id present but b9j_id None) — they will re-resolve",
            len(poisoned),
        )
        self._persist()

    def get(self, key: ResolutionKey) -> Optional[dict]:
        assert self._loaded, "ResolutionCache.load() not called"
        return self._data.get(key.as_str())

    def set(self, key: ResolutionKey, entry: dict) -> None:
        assert self._loaded, "ResolutionCache.load() not called"
        self._data[key.as_str()] = entry
        self._persist()

    def mark_stale(self, key: ResolutionKey) -> None:
        if key.as_str() in self._data:
            del self._data[key.as_str()]
            self._persist()

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def match_provider_ids(
    anchor: dict[str, str],
    others: Iterable[tuple[str, dict[str, str]]],
) -> dict[str, str]:
    """For each (name, other_ids) in `others`, find any provider id that
    `anchor` and `other_ids` share, and return {name: shared_id}.

    Provider id keys are e.g. "sr", "genius". Order of `anchor`'s providers
    determines which shared id wins when both providers match.
    """
    out: dict[str, str] = {}
    for name, other in others:
        shared: Optional[str] = None
        for provider, anchor_id in anchor.items():
            if provider in other and other[provider] == anchor_id:
                shared = anchor_id
                break
        if shared is not None:
            out[name] = shared
    return out
