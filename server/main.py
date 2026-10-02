"""FastAPI app: the JSON API under /api plus the built React frontend at /.

Run locally:   uvicorn server.main:app --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server import routes_export, routes_intake, routes_projects, routes_scenes

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(title="Lecture Animator")
# Only the Vite dev server needs CORS; the built app is served from the same origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (routes_projects, routes_intake, routes_scenes, routes_export):
    app.include_router(module.router)

if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not found."}, status_code=404)
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return JSONResponse(
            {"detail": "The frontend isn't built. Run `npm run build` in frontend/, or use the Docker image."},
            status_code=503,
        )
    candidate = (FRONTEND_DIST / full_path).resolve()
    if full_path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(index)
