from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.workspace_contracts import WorkspaceParserPlugin
from agent.workspace_types import WorkspaceDocument, WorkspacePluginContext


class DocxParser(WorkspaceParserPlugin):

    @property
    def name(self) -> str:
        return "docx"

    def supported_suffixes(self) -> set[str]:
        return {".docx", ".doc"}

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        try:
            import docx  # noqa: F401
            return True
        except ImportError:
            return False

    def signature(self, config: dict[str, Any]) -> str:
        return "docx:1"

    def parse(
        self,
        path: Path,
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceDocument | None:
        try:
            from docx import Document
        except ImportError:
            return None
        try:
            doc = Document(str(path))
        except Exception:
            return None
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return None
        text = "\n\n".join(paragraphs)
        return WorkspaceDocument(
            source_path=str(path),
            relative_path=str(path),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            text=text,
        )


def register(ctx) -> None:
    ctx.register_workspace_parser(DocxParser())
