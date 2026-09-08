"""FastAPI application entrypoint for MemOS EvalView."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path (so `testing`/`os_mem` resolve when run directly)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from testing.db import init_db
# Absolute imports keep `python -m testing.api.main` working; the
# package-relative fallback keeps uvicorn testing.api.main:app working too.
try:
    from .routes import runs_router, cases_router, stats_router, memories_router
except ImportError:
    from testing.api.routes import (  # type: ignore[no-redef]
        runs_router,
        cases_router,
        stats_router,
        memories_router,
    )

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
app.include_router(memories_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "evalview"}


# ----- Frontend static + SPA fallback -----
if _FRONTEND_DIR.exists() and any(_FRONTEND_DIR.iterdir()):
    # 单点 GET 处理（注册于 API 路由之后）：
    #   - 命中 dist 内真实文件（/assets/*.js|css 等）→ 按文件返回；
    #   - 其余路径（含 / 与 React Router 子路由直链/刷新）→ 回退 index.html；
    #   - /api 前缀未命中路由的 GET → 标准 404 JSON（与后端其它 404 一致）。
    _FRONTEND_RESOLVED = _FRONTEND_DIR.resolve()

    @app.get("/", include_in_schema=False)
    def spa_index() -> FileResponse:
        return FileResponse(_FRONTEND_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_or_static(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (_FRONTEND_DIR / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(_FRONTEND_RESOLVED)
        ):
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIR / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
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
