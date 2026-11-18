"""Retry policy for Overpass: fast server errors are retried twice (5 s, 15 s); timeouts never."""
import pytest

from orith import net, osm

SPEC = {"feature": "mosque", "area": {"around": [33.9, 8.1, 1000]}}


def fake_run_once(outcomes):
    """Replace osm._run_once with a function that plays back outcomes in order."""
    calls = []

    def _run(spec, sort_near=None, **kw):
        calls.append(1)
        o = outcomes[len(calls) - 1]
        if isinstance(o, Exception):
            raise o
        return o
    return _run, calls


def err(status):
    e = net.NetError(f"status {status}")
    e.status = status
    return e


def run(monkeypatch, outcomes):
    fn, calls = fake_run_once(outcomes)
    monkeypatch.setattr(osm, "_run_once", fn)
    logs, sleeps = [], []
    try:
        result = osm.run(SPEC, log=lambda st, sec, d: logs.append(st), sleep=sleeps.append)
    except net.NetError:
        result = None
    return result, len(calls), [s for s in logs], sleeps


def test_success_first_try(monkeypatch):
    res, n, logs, sleeps = run(monkeypatch, [([1, 2], "q", False)])
    assert res[0] == [1, 2] and n == 1 and logs == ["success"] and sleeps == []


@pytest.mark.parametrize("code", [429, 502, 503, 504])
def test_fast_server_error_is_retried_then_succeeds(monkeypatch, code):
    res, n, logs, sleeps = run(monkeypatch, [err(code), err(code), ([], "q", False)])
    assert res is not None and n == 3 and logs == ["retryable-error", "retryable-error", "retry-success"]
    assert sleeps == [5, 15]


def test_gives_up_after_two_retries(monkeypatch):
    res, n, logs, sleeps = run(monkeypatch, [err(502), err(502), err(502), ([], "q", False)])
    assert res is None and n == 3 and logs == ["retryable-error", "retryable-error", "error"] and sleeps == [5, 15]


def test_timeout_is_never_retried(monkeypatch):
    res, n, logs, sleeps = run(monkeypatch, [err("timeout"), ([], "q", False)])
    assert res is None and n == 1 and logs == ["timeout"] and sleeps == []


@pytest.mark.parametrize("status", [400, 404, "network"])
def test_other_errors_are_not_retried(monkeypatch, status):
    res, n, logs, sleeps = run(monkeypatch, [err(status), ([], "q", False)])
    assert res is None and n == 1 and logs == ["error"] and sleeps == []


def test_cached_answer_is_logged_as_cached(monkeypatch):
    res, n, logs, _ = run(monkeypatch, [([], "q", True)])
    assert logs == ["success_cached"]


def test_net_error_carries_the_status(tmp_path, monkeypatch):
    import urllib.error
    monkeypatch.setenv("ORITH_USER_AGENT", "test (contact)")
    monkeypatch.setitem(net.MIN_INTERVAL, "example.invalid", 0.0)

    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

    with pytest.raises(net.NetError) as ei:
        net.fetch_json("https://example.invalid/a", cache_dir=tmp_path, opener=opener, tries=1)
    assert ei.value.status == 502

    def opener_to(req, timeout):
        raise TimeoutError("The read operation timed out")

    with pytest.raises(net.NetError) as ei:
        net.fetch_json("https://example.invalid/b", cache_dir=tmp_path, opener=opener_to, tries=1)
    assert ei.value.status == "timeout"

def test_failover_to_the_next_endpoint_after_one_fails(monkeypatch):
    seen = []

    def _run(spec, sort_near=None, endpoint=None, **kw):
        seen.append(endpoint)
        if endpoint.endswith("a/api"):
            raise err("timeout")
        return ([7], "q", False)
    monkeypatch.setattr(osm, "_run_once", _run)
    logs = []
    res = osm.run(SPEC, log=lambda st, sec, d: logs.append((st, d.split()[0])), sleep=lambda s: None,
                  endpoint_list=["https://a/api", "https://b/api"])
    assert res[0] == [7] and seen == ["https://a/api", "https://b/api"]
    assert logs == [("timeout", "a"), ("success", "b")]


def test_all_endpoints_failing_raises_the_last_error(monkeypatch):
    monkeypatch.setattr(osm, "_run_once", lambda *a, **k: (_ for _ in ()).throw(err(400)))
    with pytest.raises(net.NetError):
        osm.run(SPEC, sleep=lambda s: None, endpoint_list=["https://a/api", "https://b/api"])


def test_endpoints_come_from_config_and_must_be_https(monkeypatch):
    monkeypatch.setattr(net, "config", lambda: {})
    assert osm.endpoints() == [osm.OVERPASS]
    monkeypatch.setattr(net, "config", lambda: {"overpass_endpoints": ["https://x/api", "https://y/api"]})
    assert osm.endpoints() == ["https://x/api", "https://y/api"]
    monkeypatch.setattr(net, "config", lambda: {"overpass_endpoints": ["http://insecure/api"]})
    with pytest.raises(osm.SpecError):
        osm.endpoints()
