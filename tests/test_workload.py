import pytest

from shutterstorm.config import ConfigError, load_settings, read_env_file
from shutterstorm.workload.probes import JOURNEYS, CsvSink
from shutterstorm.workload.stats import ProbeResult, RollingStats, percentile


def result(ts, journey="search", ok=True, status=200, ms=10.0):
    return ProbeResult(ts, journey, ok, status, ms)


def test_percentile_nearest_rank():
    assert percentile([], 0.95) is None
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([float(i) for i in range(1, 101)], 0.95) == 95.0


def test_rolling_stats_counts_errors_and_p95():
    s = RollingStats(window_s=10)
    for i in range(9):
        s.add(result(100 + i, ms=10.0))
    s.add(result(109, ok=False, status=0, ms=5000.0))
    out = s.summary("search", now=110)
    assert out["count"] == 10
    assert out["errors"] == 1
    assert out["err_pct"] == pytest.approx(10.0)
    assert out["p95_ms"] == 10.0  # failed requests do not pollute latency


def test_rolling_stats_evicts_old_results():
    s = RollingStats(window_s=10)
    s.add(result(100))
    s.add(result(125))
    assert s.summary("search", now=126)["count"] == 1


def test_rolling_stats_unknown_journey_is_empty():
    assert RollingStats().summary("nope")["count"] == 0


def test_csv_sink_flushes_each_row(tmp_path):
    path = tmp_path / "out" / "p.csv"
    sink = CsvSink(path, ["a", "b"])
    sink.write([1, 2])
    assert path.read_text().splitlines() == ["a,b", "1,2"]  # readable before close
    sink.close()


def test_journeys_have_unique_names_and_components():
    names = [j.name for j in JOURNEYS]
    assert len(names) == len(set(names))
    assert all(j.needs for j in JOURNEYS)


def test_config_refuses_non_local_target(tmp_path, monkeypatch):
    monkeypatch.delenv("IMMICH_URL", raising=False)
    env = tmp_path / ".env"
    env.write_text("IMMICH_URL=http://example.com:2283\n")
    with pytest.raises(ConfigError):
        load_settings(env)


def test_config_reads_local_env_and_hides_key(tmp_path, monkeypatch):
    monkeypatch.delenv("IMMICH_URL", raising=False)
    monkeypatch.delenv("IMMICH_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('IMMICH_URL=http://127.0.0.1:2283/\nIMMICH_API_KEY="secret123"\n')
    s = load_settings(env)
    assert s.immich_url == "http://127.0.0.1:2283"
    assert s.api_key == "secret123"
    assert "secret123" not in repr(s)
    assert read_env_file(tmp_path / "missing") == {}


def test_search_terms_are_unique_so_the_embedding_cache_cannot_hide_an_ml_outage():
    from shutterstorm.workload.probes import ProbeContext

    ctx = ProbeContext(client=None, asset_id="x")  # type: ignore[arg-type]
    terms = [ctx.next_term() for _ in range(200)]
    assert len(set(terms)) == 200
