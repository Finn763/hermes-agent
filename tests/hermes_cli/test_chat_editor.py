"""`hermes chat --editor`: compose a multi-line prompt in $EDITOR, send as ONE query.

Issue #121123: pasting multi-line text (logs, snippets, traces) into the CLI
fired one message per line. ``--editor`` opens $VISUAL/$EDITOR on a temp file
and the saved buffer becomes ``args.query`` verbatim -- a single turn.
"""

import os
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO))
try:
    from hermes_cli import main as cli_main
    from hermes_cli._parser import build_top_level_parser
finally:
    sys.path.remove(str(REPO))

HOSTILE = 'log line 1 "quoted" $(evil) `id` back\\slash\nsecond line\n\nfourth after blank'


def _parse(argv):
    built = build_top_level_parser()
    parser = built[0] if isinstance(built, tuple) else built
    return parser.parse_args(argv)


def _resolve():
    fn = getattr(cli_main, "_read_editor_query", None)
    assert callable(fn), "--editor resolver not implemented (RED)"
    return fn


class _Tty:
    def __init__(self, is_tty=True):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


def _fake_editor(tmp_path, body):
    """EDITOR value whose 'edit session' writes ``body`` to the temp file."""
    script = tmp_path / "fake_editor.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"Path(sys.argv[1]).write_text({body!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def _args_with_editor(monkeypatch, tmp_path, body, *, is_tty=True):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", _fake_editor(tmp_path, body))
    monkeypatch.setattr(sys, "stdin", _Tty(is_tty))
    return _parse(["chat", "--editor"])


def test_chat_parser_accepts_editor():
    args = _parse(["chat", "--editor"])
    assert args.editor is True
    assert args.query is None


def test_editor_mutually_exclusive_with_query(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _parse(["chat", "-q", "x", "--editor"])
    assert exc.value.code == 2


def test_editor_mutually_exclusive_with_query_file(tmp_path):
    f = tmp_path / "q.txt"
    f.write_text("hello", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _parse(["chat", "--query-file", str(f), "--editor"])
    assert exc.value.code == 2


def test_editor_buffer_becomes_single_verbatim_query(tmp_path, monkeypatch):
    """Multi-line buffer arrives as ONE args.query, byte-identical."""
    args = _args_with_editor(monkeypatch, tmp_path, HOSTILE)
    _resolve()(args)
    assert args.query == HOSTILE
    assert args.query.count("\n") == 3  # one message, not four


def test_editor_strips_comment_lines(tmp_path, monkeypatch):
    args = _args_with_editor(monkeypatch, tmp_path, "#! ignore me\nreal prompt\n")
    _resolve()(args)
    assert args.query == "real prompt"


def test_editor_empty_buffer_aborts(tmp_path, monkeypatch):
    args = _args_with_editor(monkeypatch, tmp_path, "#! only comments\n  \n")
    with pytest.raises(SystemExit) as exc:
        _resolve()(args)
    assert exc.value.code != 0
    assert args.query is None


def test_editor_conflicts_with_programmatic_query(tmp_path, monkeypatch):
    """Direct-namespace callers (bypassing argparse) still get exclusivity."""
    args = _args_with_editor(monkeypatch, tmp_path, "buffer")
    args.query = "preset"
    with pytest.raises(SystemExit) as exc:
        _resolve()(args)
    assert exc.value.code == 2


def test_editor_needs_interactive_terminal(tmp_path, monkeypatch):
    args = _args_with_editor(monkeypatch, tmp_path, "buffer", is_tty=False)
    with pytest.raises(SystemExit) as exc:
        _resolve()(args)
    assert exc.value.code == 2


def _fake_bytes_editor(tmp_path, payload: bytes):
    """EDITOR value whose 'edit session' writes raw ``payload`` bytes to the buffer."""
    script = tmp_path / "fake_editor_bytes.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"Path(sys.argv[1]).write_bytes({payload!r})\n",
        encoding="utf-8",
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def test_editor_launches_with_unquoted_platform_paths(tmp_path, monkeypatch):
    """A raw unquoted ``$EDITOR`` path pair (the common Windows form) must launch.

    ``shlex.split`` (posix=True) treats every backslash as an escape, silently
    mangling ``C:\\...`` into ``C:...``; the launch then fails and the shell
    fallback hands cmd.exe a POSIX single-quoted path. ``split_command_line``
    keeps the platform's semantics (it *is* ``shlex.split`` on POSIX).
    """
    script = tmp_path / "fake_editor_raw.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text('from raw paths\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")  # intentionally unquoted
    monkeypatch.setattr(sys, "stdin", _Tty())
    args = _parse(["chat", "--editor"])
    _resolve()(args)
    assert args.query == "from raw paths"


BOM_TEMPLATE_HEADER = (
    "\ufeff#! Compose your prompt below. Lines starting with '#!' are ignored.\n"
    "#! Save and quit to send; leave empty to cancel.\n\n"
)


def test_editor_utf8_sig_buffer_keeps_only_the_prompt(tmp_path, monkeypatch):
    """A BOM (Notepad's default utf-8-sig save) must not make the template header part of the message."""
    args = _args_with_editor(monkeypatch, tmp_path, BOM_TEMPLATE_HEADER + "real prompt\n")
    _resolve()(args)
    assert args.query == "real prompt"
    assert "\ufeff" not in args.query


def test_editor_non_utf8_buffer_does_not_abort(tmp_path, monkeypatch):
    """A latin-1 save degrades to replacement characters instead of an uncaught UnicodeDecodeError."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", _fake_bytes_editor(tmp_path, b"caf\xe9 au lait\n"))
    monkeypatch.setattr(sys, "stdin", _Tty())
    args = _parse(["chat", "--editor"])
    _resolve()(args)
    assert "caf" in args.query


def test_editor_strips_indented_comment_lines(tmp_path, monkeypatch):
    """The '#!' template filter also covers lines an editor may have indented."""
    args = _args_with_editor(monkeypatch, tmp_path, "  #! indented header\nreal prompt\n")
    _resolve()(args)
    assert args.query == "real prompt"


def test_editor_shell_fallback_quotes_path_for_platform(tmp_path, monkeypatch):
    """When the argv launch fails, the shell fallback must quote the temp path cmd.exe-style on Windows."""
    calls = []

    def fake_call(*call_args, **kwargs):
        calls.append((call_args, kwargs))
        if len(calls) == 1:
            raise OSError("argv launch failed")
        return 0

    monkeypatch.setattr(cli_main.subprocess, "call", fake_call)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f"{sys.executable} {tmp_path / 'never_runs.py'}")
    monkeypatch.setattr(sys, "stdin", _Tty())
    args = _parse(["chat", "--editor"])
    with pytest.raises(SystemExit) as exc:
        _resolve()(args)
    assert exc.value.code != 0  # the stub never writes the buffer -> empty -> abort
    assert calls[1][1].get("shell") is True
    cmdline = calls[1][0][0]
    if os.name == "nt":
        assert "'" not in cmdline  # shlex.quote emits single quotes, which cmd.exe takes literally
