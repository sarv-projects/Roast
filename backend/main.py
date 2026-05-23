import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pathlib import Path
import os

from backend.routes.analyse import router as analyse_router
from backend.routes.session import router as session_router
from backend.routes.followup import router as followup_router
from backend.routes.websocket import router as websocket_router
from backend.routes.cron import router as cron_router
from backend.routes.token_feedback import router as token_feedback_router
from backend.config import ENVIRONMENT, ALLOWED_ORIGINS
import structlog

logger = structlog.get_logger()

app = FastAPI(
    title="ROAST",
    description="Market-aware AI resume critic",
    version="0.1.0",
    docs_url="/docs" if ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if ENVIRONMENT != "production" else None,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True if ALLOWED_ORIGINS != ["*"] else False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(session_router, prefix="/api")
app.include_router(analyse_router, prefix="/api")
app.include_router(followup_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")
app.include_router(cron_router)
app.include_router(token_feedback_router, prefix="/api")


@app.on_event("startup")
async def startup():
    """Pre-warm DIVE cache and init databases on startup."""
    from backend.market_data import init_db as init_market_db
    from ingestion.database import init_db as init_ingestion_db
    init_market_db()
    init_ingestion_db()
    logger.info("databases_initialised")

    # Pre-warm DIVE cache for top combos (fire-and-forget)
    try:
        from backend.retrieval.dive import warmup_cache
        results = await warmup_cache()
        warmed = sum(1 for v in results.values() if v == "warmed")
        hits = sum(1 for v in results.values() if v == "hit")
        logger.info("cache_warmup_complete", warmed=warmed, hit=hits, total=len(results))
    except Exception as e:
        logger.warning("cache_warmup_failed", error=str(e))


@app.on_event("shutdown")
async def shutdown():
    """Clean up stale WebSocket connections on shutdown."""
    from backend.routes.ws_manager import cleanup_stale_connections
    cleanup_stale_connections()
    logger.info("shutdown_cleanup_complete")


@app.get("/health")
def health_check():
    from backend.storage.redis_client import redis
    total = redis.get("counter:total_analyses")
    return {
        "status": "ok",
        "service": "roast",
        "total_analyses": int(total) if total else 0,
    }


@app.get("/robots.txt", response_class=Response)
def robots():
    from fastapi.responses import Response
    return Response(
        content="User-agent: *\nDisallow: /api/\nAllow: /\n",
        media_type="text/plain"
    )


# ── Serve frontend static files ───────────────────────────────────────────────
_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/favicon.svg")
    def favicon():
        return FileResponse(str(_dist / "favicon.svg"))

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        # Don't intercept API or WebSocket routes
        if full_path.startswith("api/") or full_path.startswith("ws/") or full_path == "health":
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return FileResponse(str(_dist / "index.html"))
