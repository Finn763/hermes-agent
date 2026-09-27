"""``hermes models`` subcommand parser (noninteractive JSON model discovery)."""

from __future__ import annotations

from typing import Callable


def build_models_parser(subparsers, *, cmd_models: Callable) -> None:
    """Attach the ``models`` subcommand to ``subparsers``."""
    models_parser = subparsers.add_parser(
        "models",
        help="List provider models as JSON (noninteractive discovery)",
        description="Noninteractive, provider-scoped model discovery for scripts "
        "and integrations. Emits exactly one JSON document with --json; "
        "performs no network requests unless --refresh --provider ID is given.",
    )
    models_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit exactly one JSON document to stdout (required for machine output).",
    )
    models_parser.add_argument(
        "--provider",
        default=None,
        help="Limit discovery to one registered provider identifier.",
    )
    models_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Live-refresh only the given --provider (requires --provider).",
    )
    models_parser.add_argument(
        "--offline",
        action="store_true",
        help="Prohibit all network access; bundled catalog data only.",
    )
    models_parser.set_defaults(func=cmd_models)
