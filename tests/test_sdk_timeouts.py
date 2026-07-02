"""
Tests for Issue 37: module-level SDK singletons and timeout/retry configuration.

Asserts that each SDK client is constructed once with a positive timeout,
and that repeated imports return the same singleton object.
"""


def test_anthropic_dna_brief_singleton_and_timeout() -> None:
    import dna.brief as mod

    client = mod._ANTHROPIC
    assert client is mod._ANTHROPIC, "dna.brief._ANTHROPIC must be a singleton"
    assert client.timeout.read > 0


def test_anthropic_improvement_brief_singleton_and_timeout() -> None:
    import improvement.brief as mod

    client = mod._ANTHROPIC
    assert client is mod._ANTHROPIC, "improvement.brief._ANTHROPIC must be a singleton"
    assert client.timeout.read > 0


def test_anthropic_scoring_singleton_and_timeout() -> None:
    import clip_engine.scoring as mod

    client = mod._ANTHROPIC
    assert client is mod._ANTHROPIC, "clip_engine.scoring._ANTHROPIC must be a singleton"
    assert client.timeout.read > 0


def test_stripe_max_retries() -> None:
    import billing.stripe_client as mod

    # Stripe v8 uses per-client config; max_network_retries is in the requestor options.
    assert mod._STRIPE._requestor._options.max_network_retries == 3


def test_stripe_singleton() -> None:
    import billing.stripe_client as mod

    client = mod._STRIPE
    assert client is mod._STRIPE, "billing.stripe_client._STRIPE must be a singleton"


def test_voyage_singleton_and_timeout(monkeypatch) -> None:
    import dna.embeddings as mod

    monkeypatch.setattr(mod.settings, "VOYAGE_API_KEY", "test-voyage-key")
    mod._VOYAGE = None  # reset so it is rebuilt with the patched key

    client_a = mod._voyage()
    client_b = mod._voyage()
    assert client_a is client_b, "dna.embeddings._voyage() must return the same singleton"
    assert client_a._params["request_timeout"] > 0


def test_voyage_embed_callable_with_retry() -> None:
    """Assert _embed is wrapped by tenacity and remains callable."""
    from tenacity import RetryCallState  # noqa: F401 — confirms tenacity is importable

    import dna.embeddings as mod

    assert callable(mod._embed)
    assert hasattr(mod._embed, "retry"), "_embed must be decorated with @retry"


def test_voyage_embed_no_retry_on_permanent_error(monkeypatch) -> None:
    """Issue 352 Batch J: a permanent Voyage error (auth) must surface on the
    FIRST attempt — no tenacity backoff on a doomed request."""
    import pytest
    from voyageai.error import AuthenticationError

    import dna.embeddings as mod

    calls = {"n": 0}

    class _FakeClient:
        def embed(self, *args, **kwargs):
            calls["n"] += 1
            raise AuthenticationError("invalid api key")

    monkeypatch.setattr(mod, "_VOYAGE", _FakeClient())
    with pytest.raises(AuthenticationError):
        mod._embed(["x"], model="voyage-3.5", input_type="document")
    assert calls["n"] == 1, "permanent errors must not be retried"


def test_voyage_embed_retries_transient_error(monkeypatch) -> None:
    """Transient errors (rate limit) are retried up to the attempt cap."""
    from tenacity import wait_none
    from voyageai.error import RateLimitError

    import dna.embeddings as mod

    calls = {"n": 0}

    class _FakeClient:
        def embed(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError("slow down")
            return "ok"

    monkeypatch.setattr(mod, "_VOYAGE", _FakeClient())
    # retry_with keeps the predicate/stop but zeroes the wait so the test is fast.
    result = mod._embed.retry_with(wait=wait_none())(
        ["x"], model="voyage-3.5", input_type="document"
    )
    assert result == "ok"
    assert calls["n"] == 3


def test_r2_singleton_connect_timeout(monkeypatch) -> None:
    import worker.storage as mod

    # Patch settings so boto3 gets a valid-looking endpoint in test env.
    monkeypatch.setattr(mod.settings, "R2_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(mod.settings, "R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setattr(mod.settings, "R2_SECRET_ACCESS_KEY", "test-secret")
    # Reset the singleton so it is rebuilt with the patched settings.
    mod._R2 = None

    client_a = mod._r2()
    client_b = mod._r2()
    assert client_a is client_b, "worker.storage._r2() must return the same singleton"
    cfg = client_a.meta.config
    assert cfg.connect_timeout > 0
    assert cfg.read_timeout > 0
