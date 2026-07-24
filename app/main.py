from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import sentry_sdk

from app.core.config import get_settings
from app.api.routes import (
    auth, scripts, voices, campaigns, billing, projects, scenes, uploads, thumbnails,
)

settings = get_settings()

# Initialise Sentry only when a DSN is configured (works in any environment).
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="AutoScene API",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routes ────────────────────────────────────────────────────────────────────
PREFIX = "/api"
app.include_router(auth.router, prefix=PREFIX)
app.include_router(scripts.router, prefix=PREFIX)
app.include_router(voices.router, prefix=PREFIX)
app.include_router(campaigns.router, prefix=PREFIX)  # dormant (kept readable)
app.include_router(billing.router, prefix=PREFIX)
app.include_router(projects.router, prefix=PREFIX)
app.include_router(scenes.router, prefix=PREFIX)
app.include_router(uploads.router, prefix=PREFIX)
app.include_router(thumbnails.router, prefix=PREFIX)


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.environment}
