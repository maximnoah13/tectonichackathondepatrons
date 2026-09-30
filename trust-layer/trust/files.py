"""Tekst uit geüploade bestanden halen.

  .txt .md .csv  -> standaardbibliotheek
  .docx          -> standaardbibliotheek (zip + XML)
  .pdf           -> pypdf als die geïnstalleerd is, anders Claude (als er credentials zijn)
"""
import base64
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from . import engine, llm

MAX_BYTES = 15 * 1024 * 1024
SUPPORTED = (".pdf", ".docx", ".txt", ".md", ".csv")
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_text(filename, data):
    """Geeft {"text", "method", "warnings"}; ValueError bij een onleesbaar of niet-ondersteund bestand."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"bestandstype {ext or '(geen)'} is not supported; use {', '.join(SUPPORTED)}")
    if len(data) > MAX_BYTES:
        raise ValueError("file is larger than 15 MB")
    warnings = []
    if ext == ".docx":
        text, method = _docx(data), "docx (standaardbibliotheek)"
    elif ext == ".pdf":
        text, method = _pdf(data)
    else:
        text, method = _plain(data), "tekst"
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if not text:
        warnings.append("No text found. Is it a scanned PDF? That needs OCR first.")
    return {"text": text, "method": method, "warnings": warnings}


def _plain(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("this is not a valid .docx file")
    root = ET.fromstring(xml)
    paragraphs = []
    for p in root.iter(_W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == _W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == _W + "tab":
                parts.append(" ")
            elif el.tag in (_W + "br", _W + "cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages), f"pdf via pypdf ({len(reader.pages)} p.)"
        except Exception as e:  # pypdf gooit uiteenlopende fouten bij kapotte PDF's
            raise ValueError("PDF could not be read")
    if llm.available():
        out = llm.structured(
            system="Je zet documenten om naar platte tekst. Neem de tekst letterlijk over, zonder samenvatting.",
            prompt=[
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": "Geef de volledige tekst van dit document, alinea per regel."},
            ],
            schema={"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"], "additionalProperties": False},
        )
        if out:
            return out["text"], "pdf via Claude"
    raise ValueError("Reading PDF requires pypdf (pip install pypdf) or Claude with an API key. "
                     "Word (.docx) and .txt work out of the box.")


# ---------------------------------------------------------------- suggesties voor het formulier

_TYPE_HINTS = [
    (r"contract|overeenkomst|\bsla\b", "contract"),
    (r"beleid|policy|reglement", "policy"),
    (r"handleiding|procedure|werkinstructie", "procedure"),
    (r"handboek", "handbook"),
    (r"cao|\bpc ?\d+|paritair", "legal"),
    (r"mail|e-mail", "mail"),
    (r"teams|chat", "teams"),
]


def suggest(filename, text, today=None):
    today = today or date.today()
    stem = Path(filename).stem
    title = re.sub(r"[_]+", " ", stem).strip()
    title = title[:1].upper() + title[1:]
    hay = f"{stem} {text[:500]}".lower()
    source_type = next((t for pattern, t in _TYPE_HINTS if re.search(pattern, hay)), "policy")
    valid_from = None
    m = re.search(r"(?:vanaf|met ingang van|geldig vanaf|ingaande|in werking op)\s+([^.,;\n]{4,30})", text, re.I)
    if m:
        found = engine.parse_date(m.group(1).lower(), today)
        if found:
            valid_from = found.isoformat()
    version = None
    m = re.search(r"\b(v\d+(?:\.\d+)?|versie \d+(?:\.\d+)?|20\d{2})\b", stem, re.I)
    if m:
        version = m.group(1)
    return {"title": title, "source_type": source_type, "valid_from": valid_from, "version": version}


def content_type(name):
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")
