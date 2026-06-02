"""
FlowMeters FastAPI application entry point.

Start with:
    uvicorn backend.main:app --reload --port 8000

The app:
  - Connects to the MQTT broker on startup (login happens when the user signs in).
  - Serves the React SPA from ../frontend/dist/ (production build).
  - Exposes JSON API routes under /api/.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()  # load .env from the project root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

from .routers import auth, cumulative, dashboard, devices, realtime
from .scheduler import shutdown, startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup()
    yield
    shutdown()


app = FastAPI(title="FlowMeters", version="1.0.0", lifespan=lifespan)

# Allow Vite dev-server requests during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(dashboard.router)
app.include_router(cumulative.router)
app.include_router(realtime.router)

# ---------------------------------------------------------------------------
# Serve React SPA (production)
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        index = FRONTEND_DIST / "index.html"
        return FileResponse(index)
