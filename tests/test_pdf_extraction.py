"""Unit tests for autolangchat.rag.pdf_extraction against real pypdf parsing.

The route-level tests in test_admin_kb_source_routes.py mock extract_pdf_text
entirely, so these exercise the actual pypdf integration (PdfReader, encrypted
detection, text extraction) against hand-built, real PDF byte fixtures.
"""

from __future__ import annotations

import io

import pytest

from ._autolangchat_imports import clear_stale_stub_modules

clear_stale_stub_modules()

import pypdf  # noqa: E402

from autolangchat.rag.pdf_extraction import PDFExtractionError, extract_pdf_text  # noqa: E402


def _build_minimal_pdf(text: bytes = b"Hello PDF") -> bytes:
    """Build a minimal single-page PDF with a text-drawing content stream.

    Hand-rolled (rather than via a fixture file) so the exact xref byte
    offsets are computed here and always valid.
    """
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 200 200] /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    stream = b"BT /F1 24 Tf 10 100 Td (" + text + b") Tj ET" if text else b""
    objects.append(
        b"5 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n"
    )

    body = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(body))
        body += obj

    xref_start = len(body)
    xref = b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_start).encode() + b"\n%%EOF"
    )
    return body + xref + trailer


def _build_encrypted_pdf() -> bytes:
    reader = pypdf.PdfReader(io.BytesIO(_build_minimal_pdf()))
    writer = pypdf.PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.encrypt(user_password="secret")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_pdf_text_returns_real_text():
    assert extract_pdf_text(_build_minimal_pdf(b"Hello PDF")) == "Hello PDF"


def test_extract_pdf_text_rejects_garbage_bytes():
    with pytest.raises(PDFExtractionError):
        extract_pdf_text(b"not a pdf at all")


def test_extract_pdf_text_rejects_pdf_with_no_extractable_text():
    with pytest.raises(PDFExtractionError) as exc_info:
        extract_pdf_text(_build_minimal_pdf(text=b""))
    assert "no extractable text" in exc_info.value.message


def test_extract_pdf_text_rejects_encrypted_pdf():
    with pytest.raises(PDFExtractionError) as exc_info:
        extract_pdf_text(_build_encrypted_pdf())
    assert "encrypted" in exc_info.value.message
