"""Constants, result envelopes and tiny config readers shared by every STT module."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

from tools.tts_command_provider import _get_provider_section as _get_stt_section

# Log-record parity with the origin module.
logger = logging.getLogger("tools.transcription_tools")

DEFAULT_PROVIDER = "local"
DEFAULT_LOCAL_MODEL = "base"
DEFAULT_LOCAL_STT_LANGUAGE = "en"
DEFAULT_STT_MODEL = os.getenv("STT_OPENAI_MODEL", "whisper-1")
DEFAULT_GROQ_STT_MODEL = os.getenv("STT_GROQ_MODEL", "whisper-large-v3-turbo")
DEFAULT_MISTRAL_STT_MODEL = os.getenv("STT_MISTRAL_MODEL", "voxtral-mini-latest")
DEFAULT_ELEVENLABS_STT_MODEL = os.getenv("STT_ELEVENLABS_MODEL", "scribe_v2")
LOCAL_STT_COMMAND_ENV = "HERMES_LOCAL_STT_COMMAND"
LOCAL_STT_LANGUAGE_ENV = "HERMES_LOCAL_STT_LANGUAGE"
COMMON_LOCAL_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
OPENAI_BASE_URL = os.getenv("STT_OPENAI_BASE_URL", "https://api.openai.com/v1")
XAI_STT_BASE_URL = os.getenv("XAI_STT_BASE_URL", "https://api.x.ai/v1")
ELEVENLABS_STT_BASE_URL = os.getenv("ELEVENLABS_STT_BASE_URL", "https://api.elevenlabs.io/v1")
# DeepInfra STT base URL is resolved via hermes_cli.models.deepinfra_base_url (shared).

SUPPORTED_FORMATS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".oga", ".opus", ".aac", ".flac", ".caf"}
LOCAL_NATIVE_AUDIO_FORMATS = {".wav", ".aiff", ".aif"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

# Known model sets for auto-correction
OPENAI_MODELS = {"whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe", "gpt-transcribe"}
GROQ_MODELS = {"whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3-en"}

# Providers with native handlers. Kept in sync with ``agent.transcription_registry._BUILTIN_NAMES``
# (a regression test fails on drift); plugins may not register under these names and the
# dispatcher short-circuits them before command/plugin lookup.
# The plugin hook from issue #30398-style follow-up rejects plugins registering under any of these names;
# the dispatcher in ``transcribe_audio`` short-circuits them defensively as well.
BUILTIN_STT_PROVIDERS = frozenset({
    "local", "local_command", "groq", "openai", "mistral", "xai", "elevenlabs", "deepinfra"})
# Built-in providers that upload audio to a remote API.
CLOUD_STT_PROVIDERS = frozenset(BUILTIN_STT_PROVIDERS - {"local", "local_command"})


# Hub repo ids for the built-in faster-whisper sizes (``tiny`` -> ``Systran/faster-whisper-tiny``).
_WHISPER_HUB_PREFIX = "Systran/faster-whisper-"
# File faster-whisper always fetches; its presence in the cache means the snapshot is usable offline.
_WHISPER_CACHE_PROBE_FILE = "model.bin"


def _whisper_repo_id(model_name: str) -> str:
    """Hub repo id for a faster-whisper size name; already-qualified ids pass through."""
    return model_name if "/" in model_name else f"{_WHISPER_HUB_PREFIX}{model_name}"


def _is_whisper_model_cached(model_name: str) -> bool:
    """True when the model's snapshot is already usable without touching the network (#111072).

    A locally converted model directory counts as cached. Hub lookups are local-only, so a
    cache miss (or any hub hiccup) answers False and the caller falls back to the download
    path — the pre-#111072 behaviour.
    """
    path = Path(model_name)
    if "/" in model_name or "\\" in model_name:
        return path.is_dir()
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(
            _whisper_repo_id(model_name), _WHISPER_CACHE_PROBE_FILE), str)
    except Exception as exc:  # huggingface_hub absent/older: treat as "not cached"
        logger.debug("Local whisper cache probe for '%s' failed: %s", model_name, exc)
        return False


def _is_hub_error(exc: BaseException) -> bool:
    """True for a ``huggingface_hub`` error (duck-typed: no hard import of the optional package).

    Hub errors already end with their own documentation link, so Hermes' mirror hint would just be
    noise on top of them (and word-for-word overlap otherwise).
    """
    return any(cls.__module__.startswith("huggingface_hub") for cls in type(exc).__mro__)


def _hf_mirror_hint() -> str:
    """Actionable escape hatch for hosts that cannot reach huggingface.co (#111072).

    ``HF_HUB_DISABLE_XET=1`` is required alongside the mirror: with ``hf-xet`` installed,
    blob fetches are redirected to the Xet CAS bridge, which ignores ``HF_ENDPOINT`` and
    answers 401 against ``cas-server.xethub.hf.co``.
    """
    variables = "" if os.getenv("HF_ENDPOINT") else "HF_ENDPOINT=https://hf-mirror.com "
    return (f"Set {variables}HF_HUB_DISABLE_XET=1 (in the environment or $HERMES_HOME/.env) "
            "to download through a Hugging Face mirror.")


def _error_result(error: str, **extra: Any) -> Dict[str, Any]:
    """Standard failure envelope shared by every provider and validator."""
    return {"success": False, "transcript": "", "error": error, **extra}


def _ok_result(transcript: str, provider: str) -> Dict[str, Any]:
    return {"success": True, "transcript": transcript, "provider": provider}


def _lazy_ensure_quietly(dep: str) -> None:
    """Best-effort ``tools.lazy_deps.ensure(dep, prompt=False)``; failures are swallowed.
    prompt=False: a bare input() deadlocks under the interactive CLI where prompt_toolkit owns
    stdin; installs are gated by ``security.allow_lazy_installs``."""
    try:
        from tools.lazy_deps import ensure
        ensure(dep, prompt=False)
    except Exception:
        pass


def _process_error_detail(exc: "subprocess.CalledProcessError") -> str:
    """stderr > stdout > str(exc) for a failed helper binary."""
    return exc.stderr.strip() or exc.stdout.strip() or str(exc)


def _log_prompt_unsupported(label: str) -> None:
    logger.debug("%s does not support transcription prompts — proceeding without the prompt.", label)


def _config_number(cfg: Dict[str, Any], key: str, default, cast=float):
    """Read ``cfg[key]`` through *cast*, falling back to *default* on bad values."""
    try:
        return cast(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
