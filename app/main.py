import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

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


def _safe_log_path(path: str) -> str:
    """Avoid writing customer payment references into routine access logs."""
    return _PAYMENT_REF_RE.sub("<payment-reference>", path)



@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()

    # Local-only convenience. Production must use migrations instead.
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

    # UniCart only accepts small JSON/form requests. Reject oversized declared
    # bodies before FastAPI/Pydantic allocate memory to parse them.
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
