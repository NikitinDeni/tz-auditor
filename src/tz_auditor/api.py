"""Local-only audit API; the parser and its contracts remain unchanged."""

from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from tz_auditor.checks import Finding, check_numeric
from tz_auditor.parsing.docx_parser import DocxParseError, parse_docx

MAX_UPLOAD = 10 * 1024 * 1024
MAX_BODY = MAX_UPLOAD + 64 * 1024
MAX_EXPANDED = 50 * 1024 * 1024
LIMITATIONS = [
    "Проверяются только точные размеры и масса с явными единицами и объектом.",
    (
        "Диапазоны, допуски и неравенства не проверяются; "
        "отсутствие замечаний не гарантирует согласованность."
    ),
    "Общие названия объектов без идентификатора требуют ручного подтверждения.",
    "Нормативная проверка, включая 223-ФЗ, не реализована.",
]


class AuditResult(BaseModel):
    document_id: str
    filename: str
    findings: list[Finding]
    warnings: list[str]
    limitations: list[str]


class BodyLimit:
    """Bound the streamed multipart body before Starlette parses/spools it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/audit":
            return await self.app(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > MAX_BODY:
                raise HTTPException(413, "Размер запроса превышает лимит загрузки.")
            return message

        await self.app(scope, limited_receive, send)


app = FastAPI(title="TZ-Auditor", version="0.2.0")
app.add_middleware(BodyLimit)


@app.get("/health")
def health():
    return {"status": "ok"}


def audit_file(path: Path, filename: str) -> AuditResult:
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise HTTPException(413, "Слишком большой распакованный DOCX.")
            if any(e.flag_bits & 1 for e in entries):
                raise HTTPException(422, "Зашифрованные DOCX не поддерживаются.")
        document = parse_docx(path)
    except (BadZipFile, DocxParseError, KeyError, ValueError) as exc:
        raise HTTPException(422, "Некорректный или повреждённый DOCX.") from exc
    return AuditResult(
        document_id=document.document_id,
        filename=filename,
        findings=check_numeric(document),
        warnings=document.warnings,
        limitations=LIMITATIONS,
    )


@app.post("/audit", response_model=AuditResult)
async def audit(request: Request):
    async with request.form(max_files=1, max_fields=0) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(422, "Загрузите DOCX в поле file.")
        filename = (upload.filename or "").replace("\\", "/").split("/")[-1]
        if not filename.lower().endswith(".docx"):
            raise HTTPException(415, "Поддерживается только DOCX.")
        with TemporaryDirectory(prefix="tz-audit-") as folder:
            path = Path(folder) / "document.docx"
            size = 0
            with path.open("wb") as target:
                while chunk := await upload.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "Максимальный размер DOCX — 10 МиБ.")
                    target.write(chunk)
            return await run_in_threadpool(audit_file, path, filename)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
