"""FastAPI application entrypoint for MemOS EvalView."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path (so `testing`/`os_mem` resolve when run directly)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from testing.db import init_db
# Absolute imports keep `python -m testing.api.main` working; the
# package-relative fallback keeps uvicorn testing.api.main:app working too.
try:
    from .routes import runs_router, cases_router, stats_router
except ImportError:
    from testing.api.routes import runs_router, cases_router, stats_router  # type: ignore[no-redef]

_ROOT = Path(__file__).resolve().parents[3]
# 只使用 Vite 构建产物（frontend/dist），由 React+TS 前端输出
_DIST_DIR = _ROOT / "frontend" / "dist"
_FRONTEND_DIR = _DIST_DIR

app = FastAPI(title="MemOS EvalView", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure tables exist on startup
@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ----- API routers -----
app.include_router(runs_router)
app.include_router(cases_router)
app.include_router(stats_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "evalview"}


# ----- Frontend static -----
if _FRONTEND_DIR.exists() and any(_FRONTEND_DIR.iterdir()):
    # StaticFiles(html=True) 会自动把 / 映射到 index.html，并对未命中静态资源的路径执行 SPA fallback
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "msg": "frontend dist missing - please run `cd frontend && npm install && npm run build` first",
            "expected": str(_DIST_DIR),
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "testing.api.main:app",
        host="127.0.0.1",
        port=8765,
        log_level="info",
        access_log=True,
        reload=False,
    )
