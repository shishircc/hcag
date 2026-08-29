"""PDF → Markdown conversion for `crawl` (§4.4.2, §4.4.3).

Uses `pypdf` to walk pages, extract text into per-page Markdown sections,
and pull embedded raster images out into named byte buffers. Extracted
images are reported to the caller alongside filenames so the crawl core
can write them next to the Markdown output.

PDFs do not contribute outbound links — the returned `links` list is empty
by construction (§4.4.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO


_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
    (b"RIFF", ".webp"),  # webp files begin with RIFF...WEBP
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
]


def _guess_ext(data: bytes) -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    return ".bin"


@dataclass
class PdfImage:
    data: bytes
    local_filename: str  # relative filename to be written next to the .md


@dataclass
class ConvertedPdf:
    markdown: str
    images: list[PdfImage] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def convert_pdf(pdf_bytes: bytes, doc_basename: str) -> ConvertedPdf:
    """Extract text and embedded images from a PDF.

    Text is grouped by page as `## Page N` sections so the resulting
    Markdown preserves at least the coarse structure of the source. Each
    extracted image is emitted as a `![](local_filename)` reference on the
    page it came from.
    """
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf_bytes))
    lines: list[str] = []
    images: list[PdfImage] = []
    taken: set[str] = set()

    for page_idx, page in enumerate(reader.pages, start=1):
        text = ""
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        lines.append(f"## Page {page_idx}")
        lines.append("")
        if text.strip():
            lines.append(text.strip())
            lines.append("")

        page_images = []
        try:
            page_images = list(getattr(page, "images", []) or [])
        except Exception:
            page_images = []

        for img_idx, img in enumerate(page_images, start=1):
            data = getattr(img, "data", None)
            if not data:
                continue
            raw_name = (getattr(img, "name", None) or f"p{page_idx}-img{img_idx}").lstrip("/")
            if "." in raw_name:
                stem = raw_name.rsplit(".", 1)[0]
                ext = "." + raw_name.rsplit(".", 1)[1]
            else:
                stem = raw_name
                ext = _guess_ext(data)
            candidate = f"{doc_basename}-{stem}{ext}"
            n = 2
            while candidate in taken:
                candidate = f"{doc_basename}-{stem}-{n}{ext}"
                n += 1
            taken.add(candidate)
            images.append(PdfImage(data=data, local_filename=candidate))
            lines.append(f"![]({candidate})")
            lines.append("")

    md = "\n".join(lines).strip() + "\n"
    return ConvertedPdf(markdown=md, images=images, links=[])
