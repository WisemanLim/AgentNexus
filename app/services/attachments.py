"""채팅 첨부 (FR-A01, SCR-C01) — 원본은 data/attachments 에 두고 요약 근거용 텍스트를 추출한다.

표준 라이브러리만 사용(에어갭·추가 의존성 없음): txt·md·csv·json·log·yaml(텍스트), docx·xlsx(zip+XML),
pdf(Flate 스트림의 Tj/TJ 텍스트 — 스캔 이미지 PDF·암호화 PDF 는 텍스트 없음), png·jpg·gif·webp(이미지, 텍스트 없음).
"""
import hashlib
import re
import zipfile
import zlib
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.core.ids import new_id
from app.models import Attachment

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".tsv", ".xml", ".html"}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp"}
DOC_MIME = {".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
ALLOWED = TEXT_EXT | set(IMAGE_EXT) | set(DOC_MIME)
MAX_TEXT = 200_000


class AttachmentError(ValueError):
    pass


def _docx(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = []
    for p in root.iter(f"{ns}p"):
        paras.append("".join(t.text or "" for t in p.iter(f"{ns}t")))
    return "\n".join(x for x in paras if x.strip())


def _xlsx(data: bytes) -> str:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for name in sheets:
            out.append(f"# {Path(name).stem}")
            for row in ET.fromstring(z.read(name)).iter(f"{ns}row"):
                cells = []
                for c in row.iter(f"{ns}c"):
                    v = c.find(f"{ns}v")
                    if c.get("t") == "s" and v is not None:
                        cells.append(shared[int(v.text)])
                    elif c.get("t") == "inlineStr":
                        cells.append("".join(t.text or "" for t in c.iter(f"{ns}t")))
                    else:
                        cells.append(v.text if v is not None and v.text else "")
                if any(cells):
                    out.append("\t".join(cells))
    return "\n".join(out)


_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_TJ = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:[^\]]*)\]\s*TJ")
_STR = re.compile(rb"\((?:\\.|[^\\)])*\)")


def _pdf_str(raw: bytes) -> str:
    s = raw[1:-1]
    s = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b"",
                                              b"(": b"(", b")": b")", b"\\": b"\\"}[m.group(1)], s)
    s = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), s)
    for enc in ("utf-8", "latin-1"):
        try:
            return s.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _pdf(data: bytes) -> str:
    out = []
    for m in _STREAM.finditer(data):
        chunk = m.group(1)
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            pass
        for op in _TJ.finditer(chunk):
            line = "".join(_pdf_str(s.group(0)) for s in _STR.finditer(op.group(0)))
            if line.strip():
                out.append(line)
    return "\n".join(out)


def extract(name: str, data: bytes) -> tuple[str, str, str | None]:
    """(kind, mime, text) — kind: text | image | document."""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise AttachmentError(f"지원하지 않는 형식입니다: {ext or '(확장자 없음)'} — {', '.join(sorted(ALLOWED))}")
    if ext in IMAGE_EXT:
        return "image", IMAGE_EXT[ext], None
    if ext in TEXT_EXT:
        try:
            return "text", "text/plain", data.decode("utf-8")[:MAX_TEXT]
        except UnicodeDecodeError as exc:
            raise AttachmentError("UTF-8 텍스트가 아닙니다.") from exc
    try:
        text = {".docx": _docx, ".xlsx": _xlsx, ".pdf": _pdf}[ext](data)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise AttachmentError(f"{ext} 파일을 읽을 수 없습니다 ({type(exc).__name__}).") from exc
    return "document", DOC_MIME[ext], text[:MAX_TEXT]


def save(db, owner_sub: str, name: str, data: bytes, session_id: str | None = None) -> Attachment:
    limit = settings.attachment_max_mb * 1024 * 1024
    if len(data) > limit:
        raise AttachmentError(f"첨부는 {settings.attachment_max_mb}MB 이하만 가능합니다 ({len(data) // 1024}KB).")
    if not data:
        raise AttachmentError("빈 파일입니다.")
    kind, mime, text = extract(name, data)
    aid = new_id("att")
    folder = Path(settings.data_dir) / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{aid}{Path(name).suffix.lower()}"
    path.write_bytes(data)
    row = Attachment(id=aid, owner_sub=owner_sub, session_id=session_id, name=Path(name).name[:255], mime=mime,
                     size=len(data), sha256=hashlib.sha256(data).hexdigest(), path=str(path), kind=kind, text=text)
    db.add(row)
    db.commit()
    return row


def out(a: Attachment) -> dict:
    return {"id": a.id, "name": a.name, "mime_type": a.mime, "size": a.size, "kind": a.kind, "sha256": a.sha256,
            "text_chars": len(a.text or ""), "created_at": a.created_at}
