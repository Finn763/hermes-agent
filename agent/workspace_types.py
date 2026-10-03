"""Canonical workspace data types (parser stage).

Ceiling (deliberate): only the parser-stage types live here. The
chunker/embedder/reranker/retriever/index-store contracts arrive with the
workspace foundation (#5840); nothing here anticipates their shape.
Parser plugins communicate through WorkspaceDocument; the per-page
``--- Page N ---`` markers the PDF parser emits are the grouping contract
the future chunker will split on, so that format is frozen.
"""

from __future__ import annotations

import logging

# Shared set of binary file extensions the text parser skips.
# .pdf and .docx are binary but parseable, so they are deliberately
# excluded — their parser plugins claim them via supported_suffixes().
BINARY_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".zip", ".gz", ".tar", ".xz", ".7z", ".mp3", ".wav", ".ogg", ".mp4",
    ".mov", ".avi", ".sqlite", ".db", ".bin", ".exe", ".dll", ".so", ".dylib",
    ".woff", ".woff2", ".ttf", ".otf", ".doc",
})
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkspaceBlock:
    """A structural block within a parsed document."""

    kind: str  # "heading", "code", "paragraph", "table", etc.
    text: str
    heading_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None
    ordinal: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceDocument:
    """Normalized output of a parser plugin."""

    source_path: str
    relative_path: str
    media_type: str
    text: str
    blocks: tuple[WorkspaceBlock, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspacePluginContext:
    """Runtime context passed to workspace plugins."""

    hermes_home: str
    workspace_root: str
    knowledgebase_root: str
    platform: str = "cli"
    session_id: str = ""
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("workspace"))
    resolved_plugins: dict[str, str] = field(default_factory=dict)
    runtime_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginHealth:
    """Health check result for a workspace plugin."""

    healthy: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
