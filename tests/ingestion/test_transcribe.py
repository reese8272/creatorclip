"""Unit tests for ingestion/transcribe.py.

Issue 251 — GDPR / Deepgram MIP opt-out:
  Creator audio must NOT be enrolled in the Deepgram Model Improvement Partnership
  Program. The code passes mip_opt_out=True via the addons dict to transcribe_file()
  because deepgram-sdk v3 does not accept it as a PrerecordedOptions kwarg
  (SDK issue #474 — TypeError).

Also tests: docs/SUBPROCESSORS.md existence (Art. 30 record presence gate).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest


# ── regression guard: the DEFAULT backend's SDK must actually be installed ─────
# Root cause of a prod outage: deepgram-sdk was commented out of requirements.txt
# while TRANSCRIPTION_BACKEND defaulted to "deepgram". Ingest succeeded, then
# `from deepgram import …` raised ImportError on EVERY upload — invisible to the
# mocked unit tests. This guard imports the configured default backend's SDK for
# real, so a missing hard dependency fails CI instead of prod.
def test_default_transcription_backend_sdk_is_installed():
    from config import settings

    sdk_module = {"deepgram": "deepgram", "assemblyai": "assemblyai"}.get(
        settings.TRANSCRIPTION_BACKEND
    )
    if sdk_module is None:
        pytest.skip(
            f"backend {settings.TRANSCRIPTION_BACKEND!r} ships no pip SDK "
            "(e.g. whisperx self-host) — nothing to import-check"
        )
    importlib.import_module(sdk_module)  # raises ImportError if the SDK isn't installed

# ── mip_opt_out enforcement ───────────────────────────────────────────────────


def _make_deepgram_mocks() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build the fake Deepgram client + PrerecordedOptions + file stat mocks.

    We mock the `deepgram` module and the client singleton so these MIP-opt-out
    assertions don't make a network call. (deepgram-sdk IS a real installed
    dependency now — see test_default_transcription_backend_sdk_is_installed —
    but mocking keeps these unit tests hermetic and offline.)
    """
    # Minimal transcribe_file response structure that _normalize_deepgram accepts.
    fake_response = MagicMock()
    fake_response.to_dict.return_value = {
        "results": {"utterances": [], "channels": [{"alternatives": [{"words": []}]}]}
    }

    fake_rest_v1 = MagicMock()
    fake_rest_v1.transcribe_file.return_value = fake_response

    fake_client = MagicMock()
    fake_client.listen.rest.v.return_value = fake_rest_v1

    fake_stat = MagicMock()
    fake_stat.st_size = 1024 * 1024  # 1 MB — under any sane cap

    return fake_client, fake_rest_v1, fake_stat


def test_deepgram_passes_mip_opt_out_in_addons():
    """transcribe_file must be called with addons dict containing mip_opt_out=True
    as its third positional argument (deepgram-sdk v3 pattern per SDK issue #474)."""
    fake_client, fake_rest_v1, fake_stat = _make_deepgram_mocks()
    captured: dict = {}

    original_side_effect = fake_rest_v1.transcribe_file.return_value

    def fake_transcribe_file(source, opts, addons, **kwargs):
        captured["addons"] = addons
        return original_side_effect

    fake_rest_v1.transcribe_file.side_effect = fake_transcribe_file

    # deepgram-sdk is not installed in dev venv — mock the module import.
    fake_deepgram_module = MagicMock()
    fake_deepgram_module.PrerecordedOptions = MagicMock(return_value=MagicMock())

    with (
        patch("ingestion.transcribe.settings") as mock_settings,
        patch("ingestion.transcribe._deepgram_client", return_value=fake_client),
        patch("pathlib.Path.stat", return_value=fake_stat),
        patch("builtins.open", MagicMock()),
        patch.dict("sys.modules", {"deepgram": fake_deepgram_module}),
    ):
        mock_settings.TRANSCRIPTION_BACKEND = "deepgram"
        mock_settings.TRANSCRIPTION_MAX_MB = 500
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30

        from ingestion.transcribe import _transcribe_deepgram

        _transcribe_deepgram("/fake/audio.wav")

    assert "addons" in captured, "addons dict was not captured — transcribe_file not called"
    assert captured["addons"].get("mip_opt_out") is True, (
        f"mip_opt_out must be True in addons; got: {captured['addons']}"
    )


def test_deepgram_mip_opt_out_is_positional_not_kwarg():
    """addons must be passed as the third positional argument, not a keyword argument.

    deepgram-sdk v3 requires this exact call shape:
        transcribe_file(source, opts, addons, timeout=...)
    Passing addons as a keyword would silently be ignored by some SDK versions.
    """
    fake_client, fake_rest_v1, fake_stat = _make_deepgram_mocks()
    captured_args: list = []

    original_return = fake_rest_v1.transcribe_file.return_value

    def fake_transcribe_file(*args, **kwargs):
        captured_args.extend(args)
        return original_return

    fake_rest_v1.transcribe_file.side_effect = fake_transcribe_file

    fake_deepgram_module = MagicMock()
    fake_deepgram_module.PrerecordedOptions = MagicMock(return_value=MagicMock())

    with (
        patch("ingestion.transcribe.settings") as mock_settings,
        patch("ingestion.transcribe._deepgram_client", return_value=fake_client),
        patch("pathlib.Path.stat", return_value=fake_stat),
        patch("builtins.open", MagicMock()),
        patch.dict("sys.modules", {"deepgram": fake_deepgram_module}),
    ):
        mock_settings.TRANSCRIPTION_BACKEND = "deepgram"
        mock_settings.TRANSCRIPTION_MAX_MB = 500
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30

        from ingestion.transcribe import _transcribe_deepgram

        _transcribe_deepgram("/fake/audio.wav")

    # Three positional args expected: source, opts, addons
    assert len(captured_args) == 3, (
        f"Expected 3 positional args (source, opts, addons); got {len(captured_args)}"
    )
    addons_positional = captured_args[2]
    assert isinstance(addons_positional, dict), (
        f"Third positional arg must be the addons dict; got {type(addons_positional)}"
    )
    assert addons_positional.get("mip_opt_out") is True


# ── Art. 30 record presence gate ─────────────────────────────────────────────


def test_subprocessors_md_exists():
    """docs/SUBPROCESSORS.md must exist as the Art. 30 record / public sub-processor list
    (Issue 251 — GDPR Art. 28 + Art. 30)."""
    repo_root = Path(__file__).resolve().parents[2]
    subprocessors = repo_root / "docs" / "SUBPROCESSORS.md"
    assert subprocessors.exists(), "docs/SUBPROCESSORS.md is missing — Art. 30 record required"


def test_subprocessors_md_names_required_vendors():
    """The sub-processor list must name all six required vendors."""
    repo_root = Path(__file__).resolve().parents[2]
    content = (repo_root / "docs" / "SUBPROCESSORS.md").read_text()
    required_vendors = ["Anthropic", "Voyage AI", "Deepgram", "Cloudflare", "Stripe", "Google"]
    for vendor in required_vendors:
        assert vendor in content, f"SUBPROCESSORS.md is missing vendor: {vendor}"
