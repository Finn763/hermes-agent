from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from tools.tts_text_normalize import prepare_spoken_text


class _DummyAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, **kwargs):
        raise AssertionError("not used")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "dm"}


def test_prepare_spoken_text_expands_celsius_and_weather_units():
    raw = """## Christchurch today\n\n- **Now:** about **14°C**, feels like **14°C**\n- **Wind:** 9 km/h\n- **Rain:** 1.3 mm\n- **Range:** 11\u201317°C\n"""

    spoken = prepare_spoken_text(raw)

    assert "##" not in spoken
    assert "**" not in spoken
    assert "14 degrees Celsius" in spoken
    assert "11 to 17 degrees Celsius" in spoken
    assert "9 kilometres per hour" in spoken
    assert "1.3 millimetres" in spoken
    assert "°C" not in spoken
    assert "km/h" not in spoken


def test_prepare_spoken_text_polish_edge_cases():
    # Heading folds into the next sentence as a lead-in, not a bare label.
    assert prepare_spoken_text("## Weather\nIt will be sunny") == "Weather, It will be sunny."
    # Bare degree unit (no leading number) still expands.
    assert "degrees Celsius" in prepare_spoken_text("measured in °C")
    # Trailing comma is not swallowed into the amount.
    assert "300 US dollars" in prepare_spoken_text("US$300, next")
    # Real numeric rates expand, but and/or, N/A, IDs and dates are left intact.
    assert "5 dollars per month" in prepare_spoken_text("$5/month")
    assert "and/or" in prepare_spoken_text("choose and/or option")
    assert "N/A" in prepare_spoken_text("status N/A here")
    assert "2026/06/02" in prepare_spoken_text("due 2026/06/02 ok")


def test_prepare_spoken_text_rewrites_filenames_hashes_paths_ids():
    # RED for #119207: identifier-heavy spans must not be spoken verbatim.
    assert "peyton-sample-20260922" not in prepare_spoken_text(
        "I generated peyton-sample-20260922.wav today"
    )
    assert "WAV file" in prepare_spoken_text("I generated peyton-sample-20260922.wav today")
    assert "SHA-256 hash omitted" in prepare_spoken_text(
        "sha256: abc123def456789012345678901234567890 ok"
    )
    assert "abc123def456" not in prepare_spoken_text(
        "sha256: abc123def456789012345678901234567890 ok"
    )
    assert "file path omitted" in prepare_spoken_text(
        "see models/gpt-4o/checkpoint-20260922.bin today"
    )
    assert "identifier omitted" in prepare_spoken_text("released model-xyz-20260922-alpha build")
    # Speakable neighbors stay intact.
    assert "git status" in prepare_spoken_text("Use `git status` after the change.")


def test_prepare_spoken_text_keeps_addresses_and_prose_speakable():
    # Review follow-up: IPv4 must not read as "version omitted" (#119207).
    assert "127.0.0.1:8080" in prepare_spoken_text("Connect to 127.0.0.1:8080 first")
    assert "192.168.1.100" in prepare_spoken_text("It resolved to 192.168.1.100 today")
    # Slash prose stays speakable whatever its length; a real path still drops.
    assert "and/or" in prepare_spoken_text("choose and/or option")
    assert "input/output" in prepare_spoken_text("check the input/output port")
    assert "TCP/IP" in prepare_spoken_text("stack uses TCP/IP here")
    assert "N/A" in prepare_spoken_text("status N/A here")
    assert "file path omitted" in prepare_spoken_text("see src/lib/speech-text.ts today")
    # A version's optional suffix must not swallow hyphenated prose.
    spoken = prepare_spoken_text("Use version 2.0.0-or-later for compatibility")
    assert "version omitted" in spoken
    assert "or-later" in spoken
    # Real versions still collapse to the placeholder.
    assert "version omitted" in prepare_spoken_text("Upgrade to 1.2.3 now")
