from pathlib import Path

from odds_scraper.resolution import ResolutionCache, ResolutionKey, match_provider_ids


def test_loads_empty_when_file_missing(tmp_path: Path):
    cache = ResolutionCache(tmp_path / "missing.json")
    cache.load()
    assert cache.get(ResolutionKey("33660318", "prematch")) is None


def test_set_and_get_roundtrip(tmp_path: Path):
    cache_path = tmp_path / "c.json"
    cache = ResolutionCache(cache_path)
    cache.load()
    key = ResolutionKey("33660318", "prematch")
    entry = {"sr_id": "sr:match:1", "genius_id": "g-9", "sb_id": "sr:match:1",
             "b9j_id": "b9j-7", "bw_id": "sr:match:1"}
    cache.set(key, entry)
    assert cache.get(key) == entry

    cache2 = ResolutionCache(cache_path)
    cache2.load()
    assert cache2.get(key) == entry


def test_mark_stale_forces_reresolve(tmp_path: Path):
    cache = ResolutionCache(tmp_path / "c.json")
    cache.load()
    key = ResolutionKey("33660318", "prematch")
    cache.set(key, {"sr_id": "sr:match:1"})
    cache.mark_stale(key)
    assert cache.get(key) is None


def test_separate_regimes(tmp_path: Path):
    cache = ResolutionCache(tmp_path / "c.json")
    cache.load()
    pre = ResolutionKey("33660318", "prematch")
    live = ResolutionKey("33660318", "live")
    cache.set(pre, {"sr_id": "sr:match:1", "b9j_id": "internal-prematch"})
    cache.set(live, {"sr_id": "sr:match:1", "b9j_id": "genius-live"})
    assert cache.get(pre)["b9j_id"] == "internal-prematch"
    assert cache.get(live)["b9j_id"] == "genius-live"


def test_match_via_any_shared_provider_id():
    bp = {"sr": "sr:match:1", "genius": "g-9"}
    sb = {"sr": "sr:match:1"}
    b9j = {"genius": "g-9"}
    bw = {"sr": "sr:match:1"}
    matched = match_provider_ids(bp, [("sportybet", sb), ("bet9ja", b9j), ("betway", bw)])
    assert matched == {"sportybet": "sr:match:1", "bet9ja": "g-9", "betway": "sr:match:1"}


def test_no_match_returns_empty():
    bp = {"sr": "sr:match:1"}
    other = {"sr": "sr:match:99"}
    assert match_provider_ids(bp, [("sportybet", other)]) == {}


def test_load_scrubs_poisoned_b9j_entries_both_regimes(tmp_path: Path):
    """Entries with sr_id but no b9j_id were cached during a prior run when
    the bet9ja map wasn't ready (prematch) or the live-list missed the
    event transiently (live). They should be dropped on next load so
    re-resolution can pick up a fresh result. Entries with no sr_id at all
    are legitimately unresolvable and stay cached."""
    import json
    cache_path = tmp_path / "c.json"
    cache_path.write_text(json.dumps({
        "E1:prematch": {  # poisoned: prematch + sr present + b9j missing
            "sr_id": "sr:match:1", "genius_id": "",
            "sb_id": "sr:match:1", "b9j_id": None, "bw_id": "sr:match:1",
        },
        "E2:prematch": {  # healthy: b9j resolved
            "sr_id": "sr:match:2", "genius_id": "g-2",
            "sb_id": "sr:match:2", "b9j_id": "b9j-2", "bw_id": "sr:match:2",
        },
        "E3:live": {  # poisoned: live + sr present + b9j missing
            "sr_id": "sr:match:3", "genius_id": "g-3",
            "sb_id": "sr:match:3", "b9j_id": None, "bw_id": "sr:match:3",
        },
        "E4:prematch": {  # no sr_id — legitimately unresolvable, kept
            "sr_id": "", "genius_id": "",
            "sb_id": None, "b9j_id": None, "bw_id": None,
        },
    }))
    cache = ResolutionCache(cache_path)
    cache.load()
    assert cache.get(ResolutionKey("E1", "prematch")) is None
    assert cache.get(ResolutionKey("E2", "prematch"))["b9j_id"] == "b9j-2"
    assert cache.get(ResolutionKey("E3", "live")) is None
    assert cache.get(ResolutionKey("E4", "prematch"))["sr_id"] == ""
    # Persisted to disk so the scrub is one-time per file.
    cache2 = ResolutionCache(cache_path)
    cache2.load()
    assert cache2.get(ResolutionKey("E1", "prematch")) is None
    assert cache2.get(ResolutionKey("E3", "live")) is None
