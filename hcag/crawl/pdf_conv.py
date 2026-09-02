"""PDF → Markdown conversion for `crawl` (§4.4.2, §4.4.3).

Uses **PyMuPDF4LLM** (§4.4.2, tech-stack decision) to convert each page to
Markdown, and PyMuPDF directly to pull embedded raster images out into named
byte buffers. Extracted images are reported to the caller alongside filenames
so the crawl core can write them next to the Markdown output.

PyMuPDF4LLM reconstructs tables as GFM tables. That is the reason it is here:
the previous `pypdf.extract_text()` path returned a flat glyph stream with no
table model, so a ruled table arrived as prose with its column boundaries
erased — and on a table with vertically merged cells, rows silently lost the
qualifiers that applied to them, which inverts their meaning rather than merely
uglifying them.

PDFs do not contribute outbound links — the returned `links` list is empty
by construction (§4.4.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


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
    """Extract Markdown and embedded images from a PDF.

    Text is converted per page by PyMuPDF4LLM and grouped under `## Page N`
    headings, so the coarse structure of the source survives and every extract
    stays traceable to a page. Each extracted image is emitted as a
    `![](local_filename)` reference on the page it came from.
    """
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        try:
            # page_chunks keeps the page boundaries the `## Page N` headings
            # depend on; a single blob would lose them.
            chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False)
        except Exception:
            chunks = []

        lines: list[str] = []
        images: list[PdfImage] = []
        taken: set[str] = set()
        # Content hash -> filename already emitted for this document. A
        # letterhead placed on every page is ONE image referenced ten times,
        # not ten images: deduping by name alone (the previous behaviour) wrote
        # ten byte-identical copies, which inflate the packet, ride into the
        # model's context as ten multimodal blocks, and give `evalgen` a 1-in-2
        # chance of grounding a multimodal question in a decorative graphic.
        by_content: dict[str, str] = {}

        for page_idx in range(1, doc.page_count + 1):
            text = ""
            if page_idx <= len(chunks):
                chunk = chunks[page_idx - 1]
                text = (chunk.get("text") if isinstance(chunk, dict) else str(chunk)) or ""
            lines.append(f"## Page {page_idx}")
            lines.append("")
            if text.strip():
                lines.append(text.strip())
                lines.append("")

            for name, data in _page_images(doc, page_idx - 1):
                digest = hashlib.md5(data).hexdigest()
                existing = by_content.get(digest)
                if existing is not None:
                    # Same bytes, later page: reference it again, write it once.
                    lines.append(f"![]({existing})")
                    lines.append("")
                    continue
                stem, ext = name
                candidate = f"{doc_basename}-{stem}{ext}"
                n = 2
                while candidate in taken:
                    candidate = f"{doc_basename}-{stem}-{n}{ext}"
                    n += 1
                taken.add(candidate)
                by_content[digest] = candidate
                images.append(PdfImage(data=data, local_filename=candidate))
                lines.append(f"![]({candidate})")
                lines.append("")
    finally:
        doc.close()

    md = "\n".join(lines).strip() + "\n"
    return ConvertedPdf(markdown=md, images=images, links=[])


def _page_images(doc, page_index: int):
    """Yield ((stem, ext), bytes) for each raster image placed on a page."""
    try:
        placements = doc.get_page_images(page_index, full=True)
    except Exception:
        return
    for placement in placements:
        xref = placement[0]
        try:
            info = doc.extract_image(xref)
        except Exception:
            continue
        data = info.get("image")
        if not data:
            continue
        ext = "." + (info.get("ext") or "").lstrip(".") if info.get("ext") else _guess_ext(data)
        yield (f"Image{xref}", ext), data
