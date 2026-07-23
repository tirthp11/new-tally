"""Read uploaded invoice files entirely in memory.

Supported today: PDF and image. The functions accept raw bytes and never touch
the filesystem - uploads are processed in memory and discarded (see
docs/01-ARCHITECTURE.md). The structure leaves room to add further formats
such as Excel later: add a reader here and a branch in detect_kind/extractor.
"""
import base64
import io

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}


def detect_kind(filename: str) -> str:
    """Return 'pdf' or 'image'. Raises ValueError for anything else."""
    name = (filename or "").lower()
    dot = name.rfind(".")
    ext = name[dot:] if dot >= 0 else ""
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    raise ValueError(
        f"Unsupported file type '{ext or filename}'. Upload a PDF or an image."
    )


def pdf_to_images_b64(data: bytes) -> list[str]:
    """Render each page of an in-memory PDF to a base64 PNG.

    Every PDF is extracted through the vision path: rendering preserves the 2-D
    page layout, so the model reads columns by position instead of pypdf's
    linearized text, which scrambles spatially-separated columns per template.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    images = []
    for i in range(len(doc)):
        pix = doc.load_page(i).get_pixmap(dpi=300)
        images.append(base64.b64encode(pix.tobytes("png")).decode())
    doc.close()
    return images


def image_to_b64(data: bytes) -> list[str]:
    return [base64.b64encode(data).decode()]
