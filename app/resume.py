"""Local resume text extraction (PDF, DOCX) and profile extraction. Standard library only; nothing executes
macros or embedded content, and nothing leaves the machine."""
import io
import re
import zipfile
import zlib
from datetime import date
from xml.etree import ElementTree

from .skills import SKILLS, TITLES, find_skills


class ResumeError(ValueError):
    """Raised with a user-facing reason (size/format/unreadable)."""


def sniff(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
    return "unknown"


# ---------------- DOCX ----------------
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 50 * 1024 * 1024:
                raise ResumeError("The DOCX file is too large once decompressed.")
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ResumeError("The DOCX file is damaged and could not be opened.") from e
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise ResumeError("The DOCX file is damaged and could not be read.") from e
    paras = []
    for p in root.iter(W_NS + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W_NS + "t" and node.text:
                parts.append(node.text)
            elif node.tag in (W_NS + "tab",):
                parts.append(" ")
        paras.append("".join(parts))
    return "\n".join(paras)


# ---------------- PDF ----------------
STREAM_RE = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)


def _unescape_pdf_string(s: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):  # backslash
            n = s[i + 1]
            mapping = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12,
                       ord("("): 40, ord(")"): 41, ord("\\"): 92}
            if n in mapping:
                out.append(mapping[n]); i += 2; continue
            if 48 <= n <= 55:
                j = i + 1; num = b""
                while j < len(s) and len(num) < 3 and 48 <= s[j] <= 55:
                    num += bytes([s[j]]); j += 1
                out.append(int(num, 8) & 0xFF); i = j; continue
            if n in (10, 13):
                i += 2; continue
            out.append(n); i += 2; continue
        out.append(c); i += 1
    return out.decode("latin-1")


def _text_from_content(content: bytes) -> str:
    lines = []
    for block in re.findall(rb"BT(.*?)ET", content, re.S):
        parts = []
        for tok in re.finditer(rb"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]+>|T\*|Td|TD|Tm|'|\"", block):
            t = tok.group(0)
            if t.startswith(b"("):
                parts.append(_unescape_pdf_string(t[1:-1]))
            elif t.startswith(b"<"):
                hx = re.sub(rb"\s", b"", t[1:-1])
                try:
                    raw = bytes.fromhex(hx.decode())
                    parts.append(raw.decode("utf-16-be") if len(raw) % 2 == 0 and raw[:1] == b"\x00"
                                 else raw.decode("latin-1"))
                except ValueError:
                    pass
            else:
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def pdf_text(data: bytes) -> str:
    if b"%%EOF" not in data[-2048:] and b"%%EOF" not in data:
        raise ResumeError("The PDF file is incomplete or corrupted.")
    texts = []
    for m in STREAM_RE.finditer(data):
        head, body = m.group(1), m.group(2)
        if b"/FlateDecode" in head:
            try:
                body = zlib.decompressobj().decompress(body, 20 * 1024 * 1024)
            except zlib.error:
                continue
        elif b"/Filter" in head:
            continue  # images and other encodings are skipped
        if b"BT" in body:
            texts.append(_text_from_content(body))
    text = "\n".join(texts)
    if not text.strip():
        raise ResumeError("No readable text was found in the PDF. It may be a scanned image or use an "
                          "unsupported font encoding. Try exporting it again, or upload a DOCX.")
    return text


# ---------------- profile ----------------
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b(?:\s+of)?(?:\s+\w+){0,3}?\s+experience", re.I)
RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*(?:-|–|—|to)\s*((?:19|20)\d{2}|present|current|now)\b", re.I)


def extract_years(text: str) -> float:
    m = YEARS_RE.findall(text)
    if m:
        return float(max(int(x) for x in m))
    spans = []
    this_year = date.today().year
    for a, b in RANGE_RE.findall(text):
        start = int(a)
        end = this_year if not b[0].isdigit() else int(b)
        if 1970 <= start <= end <= this_year:
            spans.append((start, end))
    if not spans:
        return 0.0
    spans.sort()
    total, cur_s, cur_e = 0, spans[0][0], spans[0][1]
    for s, e in spans[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s; cur_s, cur_e = s, e
    total += cur_e - cur_s
    return float(min(total, 50))


def extract_titles(text: str):
    low = text.lower()
    found = []
    for t in TITLES:
        if re.search(r"\b" + re.escape(t) + r"\b", low) and t.title() not in found:
            found.append(t.title())
    # keep the most specific titles (drop ones contained in another)
    found = [t for t in found if not any(t != o and t.lower() in o.lower() for o in found)]
    return found[:8]


def parse_resume(data: bytes, filename: str = "") -> dict:
    if len(data) > 10 * 1024 * 1024:
        raise ResumeError("The file is larger than 10 MB. Upload a smaller PDF or DOCX.")
    if not data:
        raise ResumeError("The file is empty.")
    kind = sniff(data)
    if kind == "pdf":
        text = pdf_text(data)
    elif kind == "docx":
        text = docx_text(data)
    else:
        raise ResumeError("This is not a PDF or DOCX file. Only PDF and DOCX resumes are supported.")
    if not text.strip():
        raise ResumeError("No readable text was found in the file.")
    return {"kind": kind, "text": text, "skills": find_skills(text), "titles": extract_titles(text),
            "years": extract_years(text)}


__all__ = ["parse_resume", "ResumeError", "SKILLS"]
