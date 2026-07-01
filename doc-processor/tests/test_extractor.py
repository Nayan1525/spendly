import io
import pytest
from pypdf import PdfWriter

from app.services.extractor import extract


def make_pdf(text: str = "Hello PDF world") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestExtractText:
    def test_plain_text_mime(self):
        result = extract(b"hello world", "test.txt")
        assert result["mime_type"].startswith("text/")

    def test_plain_text_word_count(self):
        result = extract(b"one two three four five", "test.txt")
        assert result["word_count"] == 5

    def test_plain_text_preview(self):
        long_text = ("word " * 200).encode()
        result = extract(long_text, "test.txt")
        assert len(result["text_preview"]) <= 500

    def test_plain_text_no_page_count(self):
        result = extract(b"some text", "test.txt")
        assert result["page_count"] is None

    def test_file_size_bytes(self):
        content = b"hello"
        result = extract(content, "test.txt")
        assert result["file_size_bytes"] == 5

    def test_unsupported_type_preview(self):
        # Random binary data that won't be detected as text or PDF
        result = extract(bytes(range(256)), "unknown.bin")
        assert result["text_preview"] == "[unsupported type]"
        assert result["word_count"] == 0
        assert result["page_count"] is None

    def test_pdf_page_count(self):
        pdf_bytes = make_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert result["mime_type"] == "application/pdf"
        assert result["page_count"] == 1

    def test_pdf_file_size(self):
        pdf_bytes = make_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert result["file_size_bytes"] == len(pdf_bytes)
