"""CC Orchestrator — FastAPI entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("orchestrator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("CC Orchestrator starting up...")
    # TODO: init DB, load registry.yaml, start dispatcher (Phase 1)
    yield
    logger.info("CC Orchestrator shutting down...")


app = FastAPI(
    title="CC Orchestrator",
    description="Multi-instance Claude Code orchestration system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """System health check."""
    return {"status": "ok", "service": "cc-orchestrator"}
