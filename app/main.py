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

    # Always run additive tenant migrations. Production intentionally disables
    # AUTO_CREATE_TABLES, so these schema upgrades must not depend on that flag.
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS school_id VARCHAR(40) NOT NULL DEFAULT 'pau'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(32)"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS admin_role VARCHAR(24)"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS admin_school_id VARCHAR(40)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_admin_role ON users (admin_role)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_admin_school_id ON users (admin_school_id)"
            )
            await conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS admin_invites (id SERIAL PRIMARY KEY, email VARCHAR(255) NOT NULL, school_id VARCHAR(40) NOT NULL, token_hash VARCHAR(64) NOT NULL UNIQUE, created_by_user_id INTEGER NOT NULL REFERENCES users(id), created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP NOT NULL, used_at TIMESTAMP NULL)"
            )
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_invites_email ON admin_invites (email)")
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_invites_school_id ON admin_invites (school_id)")
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_invites_created_by_user_id ON admin_invites (created_by_user_id)")
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS lobbies ADD COLUMN IF NOT EXISTS school_id VARCHAR(40) NOT NULL DEFAULT 'pau'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS lobbies ADD COLUMN IF NOT EXISTS retailer VARCHAR(16) NOT NULL DEFAULT 'temu'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS lobby_passes ADD COLUMN IF NOT EXISTS shared_access_source_lobby_id INTEGER"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_school_id ON users (school_id)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_lobbies_school_id ON lobbies (school_id)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_lobbies_retailer ON lobbies (retailer)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_lobby_passes_shared_access_source_lobby_id ON lobby_passes (shared_access_source_lobby_id)"
            )
            # Retire legacy cross-retailer passes. Retailer access is no longer shared.
            await conn.exec_driver_sql(
                "UPDATE lobby_passes SET status = 'left', left_at = CURRENT_TIMESTAMP "
                "WHERE shared_access_source_lobby_id IS NOT NULL AND status = 'active'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS password_reset_code_hash VARCHAR(64)"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMP NULL"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS password_reset_attempts INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Multi-school database migration checked/applied.")

    yield
    await engine.dispose()


app = FastAPI(
    title="UniCart API",
    description="Multi-school campus group-buying platform",
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
