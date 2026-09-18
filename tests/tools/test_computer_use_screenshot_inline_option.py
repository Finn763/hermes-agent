"""``screenshot_inline=false`` — deliver the capture as a file path, not MB of base64.

Repro from the report: ``capture(mode='som')`` inlines the screenshot as a
``data:`` URL, so a ~3.9 MB PNG becomes a multi-MB base64 blob inside the tool
result — 22-64 s through the tool surface for a capture the driver itself
returns in ~1 s. No tree-walk parameter (``max_elements``/``max_depth``) can
shrink that payload; the only lever is not putting the pixels in the result.

These tests pin both halves of the contract:

* default (option absent) — byte-for-byte the previous behaviour: the
  multimodal envelope with the inline image,
* ``screenshot_inline=false`` — text result carrying ``screenshot_path``
  (a real file with the captured bytes), size and the element list, with the
  base64 payload absent.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import zlib
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.computer_use.tool as cu_tool
from tools.computer_use.backend import ActionResult, CaptureResult, UIElement

# ~1.5 MB of pixel data: same order of magnitude as the reported 3.9 MB capture,
# small enough to keep the test fast.
_IMAGE_BYTES = 1_500_000
_WIDTH, _HEIGHT = 2560, 1600


@lru_cache(maxsize=1)
def _png_bytes() -> bytes:
    """A PNG-shaped blob: real magic + IHDR (so dimensions are sniffable) and a
    large incompressible IDAT — i.e. what a real desktop screenshot weighs.
    Cached: the bytes must be stable across calls for the round-trip assertions."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", _WIDTH, _HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", os.urandom(_IMAGE_BYTES))
            + chunk(b"IEND", b""))


@pytest.fixture(scope="module")
def png() -> bytes:
    return _png_bytes()


@pytest.fixture(scope="module")
def png_b64(png) -> str:
    return base64.b64encode(png).decode("ascii")


def _capture(png_b64: str) -> CaptureResult:
    return CaptureResult(
        mode="som", width=_WIDTH, height=_HEIGHT, png_b64=png_b64,
        elements=[UIElement(index=1, role="AXButton", label="Send", bounds=(10, 20, 80, 30), app="Safari")],
        app="Safari", window_title="Inbox", image_mime_type="image/png", png_bytes_len=_IMAGE_BYTES,
    )


class _FakeBackend:
    """Minimal backend: one capture, one click (for the capture_after lane)."""

    def __init__(self, cap: CaptureResult) -> None:
        self._cap = cap
        self.captures = 0

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_available(self) -> bool: return True

    def capture(self, mode: str = "som", app=None, **kw) -> CaptureResult:
        self.captures += 1
        return self._cap

    def click(self, **kw) -> ActionResult: return ActionResult(ok=True, action="click")
    def drag(self, **kw) -> ActionResult: return ActionResult(ok=True, action="drag")
    def scroll(self, **kw) -> ActionResult: return ActionResult(ok=True, action="scroll")
    def type_text(self, text, **kw) -> ActionResult: return ActionResult(ok=True, action="type")
    def key(self, keys, **kw) -> ActionResult: return ActionResult(ok=True, action="key")
    def set_value(self, value, element=None) -> ActionResult: return ActionResult(ok=True, action="set_value")
    def list_apps(self): return []
    def list_windows(self): return []
    def focus_app(self, app, raise_window=False) -> ActionResult: return ActionResult(ok=True, action="focus_app")


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Capture cache under the test's own HERMES_HOME; no aux-vision LLM call."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(cu_tool, "_should_route_through_aux_vision", lambda: False)
    cu_tool._reset_screenshot_dedup()
    yield tmp_path
    cu_tool.reset_backend_for_tests()


def _call(args: dict, cap: CaptureResult):
    cu_tool.reset_backend_for_tests()
    backend = _FakeBackend(cap)
    with patch.object(cu_tool, "_get_backend", return_value=backend):
        return cu_tool.handle_computer_use(args), backend


def _text_result(out, label: str, png_b64: str) -> str:
    """Assert the tool result is a text payload with no inlined screenshot, and
    return it. The message quantifies the payload when the image is inlined."""
    rendered = out if isinstance(out, str) else json.dumps(out)
    assert isinstance(out, str), (
        f"{label} returned a {len(rendered)}-char multimodal result: the {len(png_b64)}-char base64 "
        "screenshot is still inlined into the tool result")
    assert "data:image" not in out, f"{label} still carries an inline data: URL ({len(out)} chars)"
    assert len(out) < 20_000, f"{label} is {len(out)} chars — the screenshot is still inlined"
    return out


def _inline_url(resp) -> str:
    return next(p for p in resp["content"] if p.get("type") == "image_url")["image_url"]["url"]


# ---------------------------------------------------------------------------
# Default behaviour is unchanged (the protection case)
# ---------------------------------------------------------------------------

class TestDefaultStillInlines:
    def test_option_absent_returns_the_multimodal_envelope(self, png_b64):
        out, _ = _call({"action": "capture", "mode": "som"}, _capture(png_b64))

        assert isinstance(out, dict), f"default capture must stay multimodal, got {type(out).__name__}"
        assert out["_multimodal"] is True
        url = _inline_url(out)
        assert url.startswith("data:image/png;base64,")
        # The pixels are still there, inlined exactly as before the option existed.
        assert len(url) >= len(png_b64)
        assert base64.b64decode(url.split(",", 1)[1]) == _png_bytes()

    def test_explicit_true_matches_the_default(self, png_b64):
        out, _ = _call({"action": "capture", "mode": "som", "screenshot_inline": True}, _capture(png_b64))
        assert isinstance(out, dict) and out["_multimodal"] is True


# ---------------------------------------------------------------------------
# The fix: file-path delivery
# ---------------------------------------------------------------------------

class TestScreenshotInlineFalse:
    def test_multi_megabyte_base64_leaves_the_tool_result(self, png_b64, tmp_path):
        out, _ = _call({"action": "capture", "mode": "som", "screenshot_inline": False}, _capture(png_b64))
        body = json.loads(_text_result(out, "capture(mode='som', screenshot_inline=false)", png_b64))

        assert body["screenshot_inlined"] is False
        # Availability is preserved: the model learns there is an image, where it is, and how big.
        shot = Path(body["screenshot_path"])
        assert shot.parent == tmp_path / "cache" / "images"
        assert shot.read_bytes() == _png_bytes()
        assert body["width"] == _WIDTH and body["height"] == _HEIGHT
        assert body["screenshot_path"] in body["summary"]
        # Element indices — the preferred way to act — survive untouched.
        assert [e["label"] for e in body["elements"]] == ["Send"]

    def test_file_lane_skips_the_aux_vision_call(self, png_b64, monkeypatch):
        """Nothing image-bearing is delivered on this lane, so the auxiliary
        vision model is not consulted for an analysis the caller did not ask for."""
        consulted = []
        monkeypatch.setattr(cu_tool, "_should_route_through_aux_vision", lambda: True)
        monkeypatch.setattr(cu_tool, "_route_capture_through_aux_vision",
                            lambda *a, **kw: consulted.append(1) or "{}")

        out, _ = _call({"action": "capture", "mode": "som", "screenshot_inline": False}, _capture(png_b64))
        body = json.loads(_text_result(out, "file-lane capture with aux vision configured", png_b64))
        assert consulted == [] and "vision_analysis" not in body

    def test_capture_after_follow_up_stays_small(self, png_b64, tmp_path, grant_computer_use_approvals):
        """The input-action follow-up capture honours the same option — otherwise
        the MB payload comes straight back on the very next call."""
        cu_tool.reset_backend_for_tests()
        backend = _FakeBackend(_capture(png_b64))
        with patch.object(cu_tool, "_get_backend", return_value=backend):
            out = cu_tool.handle_computer_use(
                {"action": "click", "element": 1, "capture_after": True, "screenshot_inline": False})

        body = json.loads(_text_result(out, "capture_after follow-up", png_b64))
        assert body["ok"] is True  # the action payload is still merged in
        assert body["screenshot_inlined"] is False
        assert Path(body["screenshot_path"]).is_file()

    def test_unwritable_cache_still_answers_honestly(self, png_b64, monkeypatch):
        monkeypatch.setattr(cu_tool, "_persist_capture_image", lambda cap: None)
        out, _ = _call({"action": "capture", "mode": "som", "screenshot_inline": False}, _capture(png_b64))
        body = json.loads(_text_result(out, "file-lane capture with an unwritable cache", png_b64))

        assert "screenshot_path" not in body
        assert "could not be written" in body["summary"]
        assert body["width"] == _WIDTH  # metadata is unaffected by the failed write

    def test_ax_capture_is_untouched(self, png_b64):
        """No image in the first place: ax captures must not grow a marker or note."""
        cap = CaptureResult(mode="ax", width=_WIDTH, height=_HEIGHT, png_b64=None, elements=[], app="Safari")
        out, _ = _call({"action": "capture", "mode": "ax", "screenshot_inline": False}, cap)
        assert "screenshot_inlined" not in json.loads(out)


    def test_gateway_media_extraction_still_finds_the_path(self, png_b64):
        """The user-facing delivery lane reads `screenshot_path` out of the tool
        result, so a path-only capture must still be findable there."""
        from gateway.media_repair import _iter_computer_use_capture_paths

        out, _ = _call({"action": "capture", "mode": "som", "screenshot_inline": False}, _capture(png_b64))
        body = json.loads(_text_result(out, "file-lane capture", png_b64))

        assert set(_iter_computer_use_capture_paths(out)) == {body["screenshot_path"]}


class TestSchemaAdvertisesTheOption:
    def test_property_is_declared_and_defaults_to_inline(self):
        from tools.computer_use.schema import COMPUTER_USE_SCHEMA

        prop = COMPUTER_USE_SCHEMA["parameters"]["properties"]["screenshot_inline"]
        assert prop["type"] == "boolean"
        assert "Default true" in prop["description"]
