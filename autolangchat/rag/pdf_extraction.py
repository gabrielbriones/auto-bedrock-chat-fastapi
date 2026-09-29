"""PDF text extraction for KB file-upload ingestion.

Used by ``POST /admin/kb/sources/file`` (``trigger_file_source`` in
``admin/admin_kb_routes.py``) to extract text from uploaded PDFs before
they're chunked/embedded like any other uploaded file. ``pypdf`` is a
required core dependency (like ``beautifulsoup4``/``html2text`` for the
web crawler); it's imported lazily here to match the codebase's
lazy-import convention for heavy modules (e.g. ``TextChunker`` in
``admin_kb_routes.py``), not because it's optional.
"""

from __future__ import annotations

import io


class PDFExtractionError(Exception):
    """Raised when PDF bytes can't be parsed into text (corrupted/encrypted/empty)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def extract_pdf_text(raw: bytes) -> str:
    """Extract text from ``raw`` PDF bytes.

    Raises :class:`PDFExtractionError` if ``raw`` isn't a valid,
    decryptable PDF with an extractable text layer.
    """
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise PDFExtractionError("PDF is encrypted")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PDFExtractionError:
        raise
    except Exception as exc:
        raise PDFExtractionError(f"failed to extract text from PDF: {exc}") from exc

    if not text.strip():
        raise PDFExtractionError("PDF contains no extractable text")

    return text
