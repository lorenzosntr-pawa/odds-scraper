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
