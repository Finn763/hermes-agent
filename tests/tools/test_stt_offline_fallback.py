"""Offline / unreachable-huggingface.co behaviour of the local faster-whisper STT path (#111072).

Contract under test:

1. A snapshotted (cached) model loads *without* a network revision lookup — the 173 s warm-cache
   stall came from huggingface_hub resolving the revision online first, even though every file was
   already on disk.
2. A cold cache keeps the historical single (network) construction; when that fails the error names
   the mirror escape hatch instead of a bare ``LocalEntryNotFoundError``.
3. A locally converted model directory and a partially-populated cache are classified correctly.

No test touches the network: the cache probe is faked (a real local-only probe never contacts the
hub), and the unreachable hub is simulated with a patched ``WhisperModel``.
"""

import os
import struct
import sys
import types
import wave
from importlib.machinery import ModuleSpec as _ModuleSpec
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

if "faster_whisper" not in sys.modules:
    _stub = types.ModuleType("faster_whisper")
    _stub.WhisperModel = MagicMock(name="WhisperModel")
    _stub.__spec__ = _ModuleSpec("faster_whisper", loader=None)
    sys.modules["faster_whisper"] = _stub


# The STT extra installs huggingface_hub; the core venv used by this suite need not have it.
# Inject a minimal stand-in (module-level, so every test in this file shares one instance).
class _FakeModule(types.ModuleType):
    def __getattr__(self, name):  # the probe only ever calls try_to_load_from_cache
        mock = MagicMock(name=name)
        setattr(self, name, mock)
        return mock


_hub = _FakeModule("huggingface_hub")
_hub.try_to_load_from_cache = MagicMock(name="try_to_load_from_cache", return_value=None)
_hub.__spec__ = _ModuleSpec("huggingface_hub", loader=None)
sys.modules.setdefault("huggingface_hub", _hub)

# The probe does ``from huggingface_hub import try_to_load_from_cache``, so patch the module that
# actually ends up loaded: the real one when the STT extra is installed, the stand-in otherwise.
_HUB_MODULE = sys.modules["huggingface_hub"]

# ``from huggingface_hub.errors import X`` must resolve to a real module when the package is absent.
if "huggingface_hub.errors" not in sys.modules:
    _errors = _FakeModule("huggingface_hub.errors")
    _errors.__spec__ = _ModuleSpec("huggingface_hub.errors", loader=None)

    class HfHubHTTPError(OSError):
        """Stand-in carrying the real class's package, which is what the hint-dedupe keys on."""

    HfHubHTTPError.__module__ = "huggingface_hub.errors"   # what ``_is_hub_error`` checks
    _errors.HfHubHTTPError = HfHubHTTPError
    sys.modules["huggingface_hub.errors"] = _errors
    _hub.errors = _errors

from tools.transcription_common import (  # noqa: E402
    _hf_mirror_hint,
    _is_whisper_model_cached,
    _whisper_repo_id,
)
from tools.transcription_local import _load_local_whisper_model  # noqa: E402
from tools.transcription_tools import _transcribe_local  # noqa: E402
import tools.transcription_tools as tt  # noqa: E402

pytestmark = pytest.mark.usefixtures("disable_lazy_stt_install")

_TIMEOUT = "ConnectTimeout: [Errno 110] Connection timed out"
_MODEL_FILE = "model.bin"


# ============================================================================
# Helpers
# ============================================================================


def _cache_snapshot(hf_home: Path, model_name: str = "base") -> Path:
    """Create the HF cache layout for one snapshot; return its directory."""
    repo = hf_home / "hub" / f"models--{_whisper_repo_id(model_name).replace('/', '--')}"
    snapshot = repo / "snapshots" / "deadbeef"
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("deadbeef", encoding="utf-8")
    return snapshot


def _offline_env(monkeypatch, hf_home: Path) -> None:
    """Point huggingface_hub at a scratch cache with no endpoint override."""
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)


@pytest.fixture
def empty_cache(tmp_path, monkeypatch):
    """A scratch HF_HOME with no snapshot in it."""
    hf_home = tmp_path / "empty-hf"
    _offline_env(monkeypatch, hf_home)
    return hf_home


@pytest.fixture
def cached_home(tmp_path, monkeypatch):
    """A scratch HF_HOME whose ``base`` snapshot holds every required file, probe faked warm."""
    hf_home = tmp_path / "hf"
    snapshot = _cache_snapshot(hf_home)
    (snapshot / _MODEL_FILE).write_bytes(b"x" * 64)
    _offline_env(monkeypatch, hf_home)
    with patch.object(_HUB_MODULE, "try_to_load_from_cache",
                      return_value=str(snapshot / _MODEL_FILE)):
        yield hf_home


def _fake_whisper(cached: bool, model=None):
    """WhisperModel stand-in that records every construction attempt.

    A ``local_files_only`` load only succeeds when *cached*; every plain (network) load raises the
    timeout seen on hosts where huggingface.co is unreachable.
    """
    calls: list = []

    def _construct(model_size_or_path, device="auto", compute_type="default", **kwargs):
        calls.append({"model": model_size_or_path, **kwargs})
        if kwargs.get("local_files_only"):
            if cached:
                return model or MagicMock(name="whisper_model")
            raise FileNotFoundError("no snapshot in the local cache")
        raise ConnectionError(_TIMEOUT)

    return _construct, calls


def _network_calls(calls):
    return [c for c in calls if not c.get("local_files_only")]


def _transcribe(audio: str, model_name: str = "base", config=None):
    """Run the real local path with the cached-model bookkeeping reset."""
    with patch.object(tt, "_local_model", None), patch.object(tt, "_local_model_name", None), \
         patch("tools.transcription_tools._load_stt_config", return_value=config or {}):
        return _transcribe_local(audio, model_name)


@pytest.fixture
def sample_wav(tmp_path):
    wav_path = tmp_path / "test.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack("<16000h", *([0] * 16000)))
    return str(wav_path)


# ============================================================================
# 1: a warm cache loads offline (the 173 s stall)
# ============================================================================


class TestWarmCacheSkipsNetwork:
    def test_cached_model_loads_without_a_revision_lookup(self, cached_home):
        model = MagicMock(name="whisper_model")
        construct, calls = _fake_whisper(cached=True, model=model)

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            assert _load_local_whisper_model("base") is model

        assert calls[0]["local_files_only"] is True, "the cached load must be offline-only"
        assert len(calls) == 1, "a warm cache must load exactly once"
        assert _network_calls(calls) == [], "a cached model must not make a network request"

    def test_cached_model_transcribes_end_to_end(self, cached_home, sample_wav):
        segment = MagicMock()
        segment.text = "cached hello"
        segment.no_speech_prob = 0.0
        segment.avg_logprob = 0.0
        model = MagicMock(name="whisper_model")
        model.transcribe.return_value = ([segment], MagicMock(language="en", duration=1.0))
        construct, calls = _fake_whisper(cached=True, model=model)

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            result = _transcribe(sample_wav)

        assert result["success"] is True, result
        assert result["transcript"] == "cached hello"
        assert _network_calls(calls) == []

    def test_cached_snapshot_that_fails_offline_falls_back_to_the_hub(self, cached_home):
        """A stale/incomplete snapshot must not brick local STT — retry with hub access."""
        model = MagicMock(name="whisper_model")
        attempts = []

        def construct(model_size_or_path, device="auto", compute_type="default", **kwargs):
            attempts.append(kwargs)
            if kwargs.get("local_files_only"):
                raise RuntimeError("Unable to open file 'model.bin'")
            return model

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            assert _load_local_whisper_model("base") is model

        assert len(attempts) == 2
        assert attempts[0]["local_files_only"] is True


# ============================================================================
# 2: cold cache — single download attempt, actionable failure
# ============================================================================


class TestColdCacheOutcomes:
    def test_cold_cache_still_downloads_in_one_attempt(self, empty_cache):
        model = MagicMock(name="whisper_model")
        calls = []

        def construct(model_size_or_path, device="auto", compute_type="default", **kwargs):
            calls.append({"model": model_size_or_path, "device": device,
                          "compute_type": compute_type, **kwargs})
            return model

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            assert _load_local_whisper_model("base", device="cpu", compute_type="float32") is model

        assert calls == [{"model": "base", "device": "cpu", "compute_type": "float32"}], (
            "a cold cache must keep the pre-#111072 single construction and omit local_files_only")

    def test_unreachable_hub_error_names_the_escape_hatch(self, empty_cache, sample_wav):
        construct, calls = _fake_whisper(cached=False)

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            result = _transcribe(sample_wav)

        assert result["success"] is False
        error = result["error"]
        assert "is not cached" in error
        assert "HF_ENDPOINT=https://hf-mirror.com" in error
        assert "HF_HUB_DISABLE_XET=1" in error
        assert "ConnectTimeout" in error
        assert len(calls) == 1, "the cold-cache path must not double-construct the model"

    def test_hint_drops_the_endpoint_once_it_is_set(self):
        with patch.dict(os.environ, {"HF_ENDPOINT": "https://hf-mirror.com"}):
            hint = _hf_mirror_hint()
        assert "HF_ENDPOINT=" not in hint
        assert "HF_HUB_DISABLE_XET=1" in hint
        assert "HF_ENDPOINT=https://hf-mirror.com" in _hf_mirror_hint()

    def test_hub_error_does_not_repeat_the_hint(self, empty_cache):
        """huggingface_hub errors carry their own doc link — appending ours would be noise."""
        from huggingface_hub.errors import HfHubHTTPError

        def construct(*args, **kwargs):
            raise HfHubHTTPError("Connection timed out (see https://huggingface.co/docs)")

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            with pytest.raises(RuntimeError) as excinfo:
                _load_local_whisper_model("base")

        assert "HF_HUB_DISABLE_XET" not in str(excinfo.value)

    def test_non_hub_failure_still_names_the_escape_hatch(self, empty_cache):
        """A local load failure (device/file) is just as likely on a blocked network."""
        def construct(*args, **kwargs):
            raise RuntimeError("Unable to open file 'model.bin'")

        with patch("faster_whisper.WhisperModel", side_effect=construct):
            with pytest.raises(RuntimeError) as excinfo:
                _load_local_whisper_model("base")

        message = str(excinfo.value)
        assert "HF_ENDPOINT=https://hf-mirror.com" in message
        assert message.count("HF_HUB_DISABLE_XET") == 1

    def test_locally_converted_model_dir_loads_offline(self, tmp_path, empty_cache):
        model_dir = tmp_path / "converted-whisper"
        model_dir.mkdir()
        assert _is_whisper_model_cached(str(model_dir)) is True

        model = MagicMock(name="whisper_model")
        construct, calls = _fake_whisper(cached=True, model=model)
        with patch("faster_whisper.WhisperModel", side_effect=construct):
            assert _load_local_whisper_model(str(model_dir)) is model
        assert _network_calls(calls) == []


# ============================================================================
# 3: cache probe semantics
# ============================================================================


class TestCacheProbe:
    def test_repo_id_mapping(self):
        assert _whisper_repo_id("base") == "Systran/faster-whisper-base"
        assert _whisper_repo_id("Systran/faster-whisper-large-v3") == "Systran/faster-whisper-large-v3"

    def test_missing_cache_is_not_cached(self, empty_cache):
        assert _is_whisper_model_cached("base") is False

    def test_partial_cache_is_not_cached(self, tmp_path, monkeypatch):
        """config.json without model.bin can't be loaded — must not be reported as cached."""
        hf_home = tmp_path / "hf"
        snapshot = _cache_snapshot(hf_home)
        (snapshot / "config.json").write_bytes(b"{}")
        _offline_env(monkeypatch, hf_home)

        def _local_probe(repo_id, filename, **_kwargs):
            target = snapshot / filename
            return str(target) if target.exists() else None

        with patch.object(_HUB_MODULE, "try_to_load_from_cache", side_effect=_local_probe):
            assert _is_whisper_model_cached("base") is False
            (snapshot / _MODEL_FILE).write_bytes(b"x" * 64)
            assert _is_whisper_model_cached("base") is True

    def test_probe_failure_is_not_cached(self):
        with patch.object(_HUB_MODULE, "try_to_load_from_cache", side_effect=OSError("hub exploded")):
            assert _is_whisper_model_cached("base") is False
