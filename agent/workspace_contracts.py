"""Parser-stage plugin contract for the workspace pipeline.

Ceiling (deliberate): only the parser ABC lives here. Chunker, embedder,
reranker, retriever, and index-store ABCs arrive with the workspace
foundation (#5840); they are not stubbed out in advance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agent.workspace_types import (
    PluginHealth,
    WorkspaceDocument,
    WorkspacePluginContext,
)


class WorkspacePlugin(ABC):
    """Minimal base shared by all workspace category plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g. 'builtin-text')."""

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        """Return True if this plugin can run in the current environment."""
        return True

    def warm_up(self, config: dict[str, Any], context: WorkspacePluginContext) -> None:
        """Optional eager initialization (model loading, connection pooling)."""

    def healthcheck(self, config: dict[str, Any], context: WorkspacePluginContext) -> PluginHealth:
        """Return health status.  Default: healthy."""
        return PluginHealth(healthy=True)

    def signature(self, config: dict[str, Any]) -> str:
        """Return a string that changes when plugin behaviour changes.

        Used for index invalidation.  Default: plugin name.
        """
        return self.name

    def config_schema(self) -> list[dict[str, Any]]:
        """Return JSON-schema-style config field descriptors."""
        return []


class WorkspaceParserPlugin(WorkspacePlugin):
    """Reads files and normalizes them into WorkspaceDocuments."""

    @abstractmethod
    def supported_suffixes(self) -> set[str]:
        """File extensions this parser handles (e.g. {'.md', '.txt'})."""

    @abstractmethod
    def parse(
        self,
        path: Path,
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceDocument | None:
        """Parse a file into a WorkspaceDocument, or None if unsupported."""
