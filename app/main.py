import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import auth, lobbies, payments

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("unicart")

app = FastAPI(
    title="UniCart API",
    description="Campus group-buying platform — PAU edition",
    version="1.0.0",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Allowed origins — covers local dev AND the live GitHub Pages frontend.
# To add more origins, update BACKEND_CORS_ORIGINS in your Render env vars:
#   e.g. https://epic-ice-fire.github.io,http://localhost:57318
_raw_origins = os.getenv(
    "BACKEND_CORS_ORIGINS",
    "https://epic-ice-fire.github.io",
)
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# Always include localhost variants for local development
_local_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://localhost:57318",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]

_all_origins = list(set(_allowed_origins + _local_origins))

logger.info(f"CORS allowed origins: {_all_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_all_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} "
        f"→ {response.status_code} ({duration_ms}ms)"
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
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