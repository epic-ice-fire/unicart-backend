import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from html import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.routers import auth, lobbies, payments
from app.config import settings
from app.db import Base, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("unicart")

_PAYMENT_REF_RE = re.compile(r"unicart_(?:entry|item)_[A-Za-z0-9_-]+")
_CALLBACK_CLOSE_BUTTON = '<button onclick="window.close()">Close tab</button>'


def _safe_log_path(path: str) -> str:
    return _PAYMENT_REF_RE.sub("<payment-reference>", path)


async def _callback_with_return_link(response: Response) -> Response:
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        return response

    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        raw_body = getattr(response, "body", b"")
        if isinstance(raw_body, str):
            raw_body = raw_body.encode("utf-8")
    else:
        chunks: list[bytes] = []
        async for chunk in iterator:
            if isinstance(chunk, str):
                chunks.append(chunk.encode("utf-8"))
            elif isinstance(chunk, memoryview):
                chunks.append(chunk.tobytes())
            else:
                chunks.append(bytes(chunk))
        raw_body = b"".join(chunks)

    body = raw_body.decode("utf-8", errors="replace")
    app_url = escape(settings.PUBLIC_APP_URL, quote=True)
    return_link = (
        f'<a href="{app_url}" '
        'style="display:inline-block;text-decoration:none;border:none;border-radius:12px;'
        'padding:12px 18px;background:#111827;color:white;font-weight:700;'
        'cursor:pointer;margin-top:22px;margin-right:8px">Return to UniCart</a>'
    )
    body = body.replace(_CALLBACK_CLOSE_BUTTON, return_link)

    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    return HTMLResponse(
        content=body,
        status_code=response.status_code,
        headers=headers,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()

    if settings.AUTO_CREATE_TABLES:
        import app.models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables checked/created successfully.")

    yield
    await engine.dispose()


app = FastAPI(
    title="UniCart API",
    description="Campus group-buying platform — PAU edition",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:12]
    start = time.perf_counter()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header."},
                headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
            )
        if declared_size < 0:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header."},
                headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
            )
        if declared_size > settings.MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body is too large."},
                headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
            )

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.exception(
            "[%s] %s %s -> 500 (%.1fms)",
            request_id,
            request.method,
            _safe_log_path(request.url.path),
            duration_ms,
        )
        raise

    if request.url.path == "/payments/callback":
        response = await _callback_with_return_link(response)

    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "[%s] %s %s -> %s (%.1fms)",
        request_id,
        request.method,
        _safe_log_path(request.url.path),
        response.status_code,
        duration_ms,
    )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", _safe_log_path(request.url.path))
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
        headers={"Cache-Control": "no-store"},
    )


app.include_router(auth.router)
app.include_router(lobbies.router)
app.include_router(payments.router)


@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "service": "UniCart API", "version": "1.0.0"}


@app.get("/health", tags=["health"])
def health():
    return {"status": "healthy"}
