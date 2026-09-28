"""Shims to suppress old updater work until relaunch. New code must not use these."""

from typing import NoReturn


def ensure(feature: str, *, prompt: bool = True) -> NoReturn:
    # Shim to suppress old updater work until relaunch. Do not claim readiness.
    # Preserve the dependency-unavailable failure without claiming a completed install.
    raise ImportError("Dependencies are unknown to this old updater. Please relaunch Hermes.")


def install_specs(specs: list[str] | tuple[str, ...], *, timeout: int = 300) -> NoReturn:
    # Shim to suppress old updater work until relaunch. Do not install,
    # run the updater, or report success. Exiting here turned every plugin
    # availability probe into a gateway-draining update loop (SystemExit
    # slips past `except Exception`), so raise a plain error like ensure().
    raise ImportError("Dependencies are unknown to this old updater. Please relaunch Hermes.")
