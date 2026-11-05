"""Test helpers: build real PDF/DOCX bytes and a fake network (records every outbound request)."""
import io
import json
import zipfile
import zlib
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

RESUME_LINES = [
    "Asha Verma - Senior Software Engineer",
    "Summary: Software engineer with 6 years of experience building data platforms.",
    "Skills: Python, AWS, SQL, Docker, Kubernetes, React",
    "Experience: Backend Developer, Acme Corp 2019 - present",
    "Secret marker line 7f3a9c unique resume text",
]


def make_pdf(lines=RESUME_LINES, pages=2) -> bytes:
    objs = []
    kids = []
    content_ids = []
    n = 3
    for p in range(pages):
        ops = ["BT /F1 11 Tf 72 760 Td 14 TL"]
        for line in (lines if p == 0 else [f"Page {p + 1} continued", *lines[2:3]]):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = zlib.compress("\n".join(ops).encode("latin-1"))
        content_ids.append((n, stream))
        kids.append(n + 1)
        n += 2
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}

    def obj(i, body: bytes):
        offsets[i] = out.tell()
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {pages} >>".encode())
    for cid, stream in content_ids:
        obj(cid, f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode() + stream + b"\nendstream")
        obj(cid + 1, f"<< /Type /Page /Parent 2 0 R /Contents {cid} 0 R >>".encode())
    xref = out.tell()
    out.write(f"xref\n0 {n}\ntrailer << /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(lines=RESUME_LINES) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in lines)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeNet:
    """Replaces sources.FETCH. Maps host substrings to fixture bodies or exceptions; records requests."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, url, timeout):
        self.requests.append(url)
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, BaseException):
                    raise val
                return val if isinstance(val, str) else json.dumps(val)
        raise AssertionError(f"unexpected URL {url}")
