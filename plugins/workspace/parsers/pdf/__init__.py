from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.workspace_contracts import WorkspaceParserPlugin
from agent.workspace_types import WorkspaceDocument, WorkspacePluginContext


class PdfParser(WorkspaceParserPlugin):

    @property
    def name(self) -> str:
        return "pdf"

    def supported_suffixes(self) -> set[str]:
        return {".pdf"}

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except ImportError:
            pass
        try:
            import pdfplumber  # noqa: F401
            return True
        except ImportError:
            pass
        return False

    def signature(self, config: dict[str, Any]) -> str:
        return "pdf:1"

    def parse(
        self,
        path: Path,
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceDocument | None:
        text = self._try_fitz(path)
        if text is None:
            text = self._try_pdfplumber(path)
        if text is None:
            return None
        if not text.strip():
            return None
        return WorkspaceDocument(
            source_path=str(path),
            relative_path=str(path),
            media_type="application/pdf",
            text=text,
        )

    # ------------------------------------------------------------------
    # Backend helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_fitz(path: Path) -> str | None:
        try:
            import fitz
        except ImportError:
            return None
        try:
            doc = fitz.open(str(path))
        except Exception:
            return None
        pages: list[str] = []
        for i, page in enumerate(doc, start=1):
            page_text = page.get_text()
            pages.append(f"\n\n--- Page {i} ---\n\n{page_text}")
        doc.close()
        return "".join(pages).strip()

    @staticmethod
    def _try_pdfplumber(path: Path) -> str | None:
        try:
            import pdfplumber
        except ImportError:
            return None
        try:
            pdf = pdfplumber.open(str(path))
        except Exception:
            return None
        pages: list[str] = []
        for i, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            pages.append(f"\n\n--- Page {i} ---\n\n{page_text}")
        pdf.close()
        return "".join(pages).strip()


def register(ctx) -> None:
    ctx.register_workspace_parser(PdfParser())
