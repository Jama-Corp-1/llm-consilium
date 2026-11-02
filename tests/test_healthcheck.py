import httpx

import healthcheck as hc


def test_check_models_all_present():
    assert hc.check_models(set(hc.EXPECTED_ALIASES), hc.EXPECTED_ALIASES).ok


def test_check_models_reports_missing():
    partial = set(hc.EXPECTED_ALIASES) - {"council/groq-gpt-oss-20b"}
    result = hc.check_models(partial, hc.EXPECTED_ALIASES)
    assert not result.ok
    assert "council/groq-gpt-oss-20b" in result.detail


def test_classify_completion_ok():
    assert hc.classify_completion("groq", 200, None).ok


def test_classify_completion_401():
    result = hc.classify_completion("groq", 401, None)
    assert not result.ok and "401" in result.detail


def test_classify_completion_429():
    result = hc.classify_completion("groq", 429, None)
    assert not result.ok and "429" in result.detail


def test_classify_completion_transport_error():
    result = hc.classify_completion("groq", None, "timeout")
    assert not result.ok and result.detail == "timeout"


def test_summarize_exit_codes():
    assert hc.summarize([hc.ProbeResult("a", True, "x")]) == 0
    mixed = [hc.ProbeResult("a", True, "x"), hc.ProbeResult("b", False, "y")]
    assert hc.summarize(mixed) == 1


def test_probe_models_with_mock_transport():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": a} for a in hc.EXPECTED_ALIASES]})

    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    assert hc._probe_models(client).ok


def test_probe_completion_with_mock_transport():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    assert hc._probe_completion(client, "groq", "council/groq-gpt-oss-20b").ok
