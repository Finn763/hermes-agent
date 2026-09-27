"""#105418: macOS background shell must not use login -lic (alias rewrite)."""
import tools.process_registry as pr


def test_macos_background_uses_nonlogin_c(monkeypatch):
    monkeypatch.setattr(pr, "_IS_WINDOWS", False)
    monkeypatch.setattr(pr, "_IS_LINUX", False)
    monkeypatch.setattr(pr, "_find_shell", lambda: "/bin/zsh")
    reg = pr.ProcessRegistry.__new__(pr.ProcessRegistry)
    sess = pr.ProcessSession(
        id="x", command="echo hi", task_id="", owner_task_id="",
        session_key="", cwd="/tmp", started_at=0.0,
    )
    assert reg._scope_argv(sess, "echo hi", "u", "Local") == [
        "/bin/zsh", "-c", "set +m; echo hi",
    ]


def test_linux_background_keeps_login_lic(monkeypatch):
    monkeypatch.setattr(pr, "_IS_WINDOWS", False)
    monkeypatch.setattr(pr, "_IS_LINUX", True)
    monkeypatch.setattr(pr, "_find_shell", lambda: "/bin/bash")
    monkeypatch.setattr(pr, "_is_supervised_gateway_process", lambda: False)
    reg = pr.ProcessRegistry.__new__(pr.ProcessRegistry)
    sess = pr.ProcessSession(
        id="x", command="echo hi", task_id="", owner_task_id="",
        session_key="", cwd="/tmp", started_at=0.0,
    )
    assert reg._scope_argv(sess, "echo hi", "u", "Local") == [
        "/bin/bash", "-lic", "set +m; echo hi",
    ]
