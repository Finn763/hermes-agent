"""``busy_input_mode: queue`` — a follow-up with media must not be absorbed into the pending head.

Regression coverage for the media-merge branch of
``GatewayBusySessionMixin._queue_or_replace_pending_event``: that branch is shared by every
busy/queue/steer-fallback call site and used to trigger on *any* media on either side, so three
Telegram voice notes sent during a long turn collapsed into the head event and were delivered as
ONE follow-up turn (their audio transcribed in sequence). The FIFO contract from #28503 says each
follow-up is its own turn in arrival order.

The merge branch stays for its real purposes:
  * photo bursts / albums (PHOTO on either side), and
  * a TEXT follow-up that is the caption of the pending media event.
"""

from unittest.mock import MagicMock

from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import Platform


class _PendingAdapter:
    """Only ``_pending_messages`` is touched by the busy-queue path."""

    def __init__(self):
        self._pending_messages = {}


def _make_runner_and_adapter():
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._queued_events = {}
    adapter = _PendingAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    return runner, adapter


def _event(media_path: str, media_type: str, message_type: MessageType, text: str = "") -> MessageEvent:
    # profile=None: a MagicMock auto-attribute reads as a truthy stamped profile and trips
    # fail-closed adapter resolution.
    source = MagicMock(chat_id="c1", platform=Platform.TELEGRAM, profile=None)
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=source,
        media_urls=[media_path],
        media_types=[media_type],
        message_id=f"m-{media_path}",
    )


def _voice(path: str) -> MessageEvent:
    return _event(path, "audio/ogg", MessageType.VOICE)


def _photo(path: str, caption: str = "") -> MessageEvent:
    return _event(path, "image/jpeg", MessageType.PHOTO, text=caption)


def _text(text: str) -> MessageEvent:
    source = MagicMock(chat_id="c1", platform=Platform.TELEGRAM, profile=None)
    return MessageEvent(text=text, message_type=MessageType.TEXT, source=source, message_id=f"m-{text}")


class TestVoiceFollowupsStayDistinct:
    """Three voice notes must stay three FIFO items, each starting its own turn."""

    def test_three_voice_followups_are_three_fifo_items(self):
        runner, adapter = _make_runner_and_adapter()
        session_key = "telegram:user:voice-fifo"

        for path in ("/tmp/voice-1.ogg", "/tmp/voice-2.ogg", "/tmp/voice-3.ogg"):
            runner._queue_or_replace_pending_event(session_key, _voice(path))

        head = adapter._pending_messages[session_key]
        assert head.media_urls == ["/tmp/voice-1.ogg"], (
            "voice notes must not be merged into the pending head event; "
            f"head carries {head.media_urls!r}"
        )
        assert [e.media_urls for e in runner._queued_events[session_key]] == [
            ["/tmp/voice-2.ogg"],
            ["/tmp/voice-3.ogg"],
        ]
        assert runner._queue_depth(session_key, adapter=adapter) == 3

    def test_voice_then_voice_keeps_second_as_overflow_item(self):
        """The minimal repro: two voice notes, two items."""
        runner, adapter = _make_runner_and_adapter()
        session_key = "telegram:user:voice-pair"

        runner._queue_or_replace_pending_event(session_key, _voice("/tmp/voice-a.ogg"))
        runner._queue_or_replace_pending_event(session_key, _voice("/tmp/voice-b.ogg"))

        assert adapter._pending_messages[session_key].media_urls == ["/tmp/voice-a.ogg"]
        assert [e.media_urls for e in runner._queued_events[session_key]] == [["/tmp/voice-b.ogg"]]

    def test_video_and_document_followups_are_not_merged(self):
        """Non-photo media is independent regardless of kind (video, document)."""
        runner, adapter = _make_runner_and_adapter()
        session_key = "telegram:user:mixed-media"

        runner._queue_or_replace_pending_event(session_key, _event("/tmp/clip.mp4", "video/mp4", MessageType.VIDEO))
        runner._queue_or_replace_pending_event(session_key, _event("/tmp/notes.pdf", "application/pdf", MessageType.DOCUMENT))

        assert adapter._pending_messages[session_key].media_urls == ["/tmp/clip.mp4"]
        assert [e.media_urls for e in runner._queued_events[session_key]] == [["/tmp/notes.pdf"]]


class TestMergeBranchStillAbsorbsPhotosAndCaptions:
    """The merge branch keeps its two legitimate uses (protects the pre-existing behavior)."""

    def test_photo_burst_still_merges_into_one_album_event(self):
        runner, adapter = _make_runner_and_adapter()
        session_key = "telegram:user:photo-album"

        runner._queue_or_replace_pending_event(session_key, _photo("/tmp/album-1.jpg"))
        runner._queue_or_replace_pending_event(session_key, _photo("/tmp/album-2.jpg"))

        head = adapter._pending_messages[session_key]
        assert head.media_urls == ["/tmp/album-1.jpg", "/tmp/album-2.jpg"]
        assert head.message_type == MessageType.PHOTO
        assert runner._queue_depth(session_key, adapter=adapter) == 1
        assert not runner._queued_events.get(session_key)

    def test_text_followup_still_merges_as_caption_of_pending_photo(self):
        runner, adapter = _make_runner_and_adapter()
        session_key = "telegram:user:photo-caption"

        runner._queue_or_replace_pending_event(session_key, _photo("/tmp/cap.jpg", caption="first line"))
        runner._queue_or_replace_pending_event(session_key, _text("second line"))

        head = adapter._pending_messages[session_key]
        assert head.media_urls == ["/tmp/cap.jpg"]
        assert "first line" in head.text and "second line" in head.text
        assert runner._queue_depth(session_key, adapter=adapter) == 1

    def test_text_followup_still_merges_as_caption_of_pending_voice(self):
        """Caption merging is not photo-only — a pending voice note absorbs the text too."""
        runner, adapter = _make_runner_and_adapter()
        session_key = "telegram:user:voice-caption"

        runner._queue_or_replace_pending_event(session_key, _voice("/tmp/voice-cap.ogg"))
        runner._queue_or_replace_pending_event(session_key, _text("context for the note"))

        head = adapter._pending_messages[session_key]
        assert head.media_urls == ["/tmp/voice-cap.ogg"]
        assert "context for the note" in head.text
        assert runner._queue_depth(session_key, adapter=adapter) == 1
