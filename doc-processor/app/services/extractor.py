import io
import structlog
import magic
from pypdf import PdfReader

logger = structlog.get_logger(__name__)

TEXT_MIME_PREFIXES = ("text/",)
PDF_MIME = "application/pdf"
PREVIEW_LENGTH = 500


def extract(content: bytes, filename: str = "") -> dict:
    file_size_bytes = len(content)
    mime_type = magic.from_buffer(content, mime=True)

    page_count = None
    word_count = 0
    text_preview = ""

    if mime_type == PDF_MIME:
        text, page_count = _extract_pdf(content)
        word_count = len(text.split())
        text_preview = text[:PREVIEW_LENGTH]

    elif any(mime_type.startswith(p) for p in TEXT_MIME_PREFIXES):
        text = _extract_text(content)
        word_count = len(text.split())
        text_preview = text[:PREVIEW_LENGTH]

    else:
        text_preview = "[unsupported type]"

    logger.info(
        "extraction_completed",
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        word_count=word_count,
    )

    return {
        "mime_type":       mime_type,
        "file_size_bytes": file_size_bytes,
        "page_count":      page_count,
        "word_count":      word_count,
        "text_preview":    text_preview,
    }


def _extract_pdf(content: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages), len(reader.pages)


def _extract_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1", errors="replace")
