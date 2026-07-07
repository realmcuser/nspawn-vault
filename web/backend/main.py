import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import models
import auth_routes
import vault_routes
from database import engine

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="nspawn-vault-web")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(vault_routes.router)

# Serve the built frontend. Caddy normally handles this in production (see
# Caddyfile), but keeping this here too means the app also works standalone
# behind a plain reverse proxy that forwards everything to uvicorn.
_possible_dirs = ["../frontend/dist", "frontend/dist", "../dist", "dist"]
_dist_dir = next((d for d in _possible_dirs if os.path.isdir(d)), None)

if _dist_dir:
    _assets_dir = os.path.join(_dist_dir, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = os.path.join(_dist_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_dist_dir, "index.html"))
