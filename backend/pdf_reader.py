import re
import fitz
from urllib.parse import urlparse


def clean_text(raw: str) -> str:
    """
    Fix common PDF extraction messiness:
    - collapse 3+ blank lines into 2
    - strip trailing whitespace from each line
    - remove lines that are purely whitespace
    """
    lines = raw.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        cleaned.append(stripped)

    rejoined = "\n".join(cleaned)

    # collapse 3+ consecutive newlines → 2
    rejoined = re.sub(r"\n{3,}", "\n\n", rejoined)

    return rejoined.strip()


def is_valid_resume_text(text: str) -> tuple[bool, str]:
    """
    Check if extracted text meets our limits.
    Returns (is_valid, reason_if_not).
    """
    from backend.config import MIN_CHARS, MAX_CHARS

    if len(text) < MIN_CHARS:
        return False, f"Too little text extracted ({len(text)} chars). Is this a scanned/image PDF?"
    if len(text) > MAX_CHARS:
        return False, f"Resume too long ({len(text)} chars). Maximum allowed is {MAX_CHARS}."
    return True, ""


def extract_links(pdf_path: str) -> dict:
    """
    Extract hyperlinks from the annotation layer.
    This is how we get actual LinkedIn and GitHub URLs —
    they are NOT in the text layer.
    """
    links = {
        "page_count": 0,
        "validation_error": None,
        "all_urls": [],
        "linkedin": None,
        "github": None,
    }

    with fitz.open(pdf_path) as doc:
        if doc.is_encrypted:
            links["validation_error"] = "PDF is encrypted. Please upload an unencrypted resume."
            return links
        links["page_count"] = len(doc)
        for page_number in range(len(doc)):
            page = doc.load_page(page_number)
            # get_links() returns annotation-layer links
            for link in page.get_links():
                uri = link.get("uri", "")
                if not uri or uri.startswith("mailto:"):
                    continue

                links["all_urls"].append(uri)

                parsed = urlparse(uri)
                domain = parsed.netloc.lower()

                if "linkedin.com" in domain and links["linkedin"] is None:
                    links["linkedin"] = uri
                if "github.com" in domain and links["github"] is None:
                    links["github"] = uri

    return links


def extract_text_from_pdf(pdf_path: str) -> dict:
    """
    Full pipeline: open PDF → extract text → clean → validate.
    """
    from backend.config import MAX_PAGES, MAX_FILE_SIZE_MB
    import os

    result = {
        "page_count": 0,
        "full_text": "",
        "pages": [],
        "is_valid": False,
        "validation_error": None,
        "error": None,
    }

    # check file size before even opening
    size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        result["validation_error"] = f"File too large ({size_mb:.1f}MB). Max is {MAX_FILE_SIZE_MB}MB."
        return result

    try:
        with fitz.open(pdf_path) as doc:
            result["page_count"] = len(doc)

            if len(doc) > MAX_PAGES:
                result["validation_error"] = f"Too many pages ({len(doc)}). Max is {MAX_PAGES}. Please upload a resume, not a CV."
                return result

            all_text_parts = []
            for page_number in range(len(doc)):
                page = doc.load_page(page_number)
                page_text_raw = page.get_text("text")
                page_text = page_text_raw if isinstance(page_text_raw, str) else ""
                cleaned = clean_text(page_text)
                result["pages"].append({
                    "page_number": page_number + 1,
                    "text": cleaned,
                    "char_count": len(cleaned),
                })
                all_text_parts.append(cleaned)

            result["full_text"] = "\n\n".join(all_text_parts)

    except Exception as e:
        result["error"] = str(e)
        return result

    valid, reason = is_valid_resume_text(result["full_text"])
    result["is_valid"] = valid
    if not valid:
        result["validation_error"] = reason

    return result