"""App entry: mounts the API, the connector WebSocket, and the static frontend."""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api.v1 import admin, auth, connector_ws, documents, download, masters, vouchers
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.security import hash_password
from app.db.base import import_all_models
from app.db.session import get_sessionmaker
from app.models import User

setup_logging()
log = logging.getLogger(__name__)

app = FastAPI(title="ABS Tally Suite", docs_url="/api/docs", openapi_url="/api/openapi.json")

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(documents.router, prefix=API_PREFIX)
app.include_router(masters.router, prefix=API_PREFIX)
app.include_router(vouchers.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)
app.include_router(download.router, prefix=API_PREFIX)
app.include_router(connector_ws.router, prefix=API_PREFIX)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def bootstrap_admin() -> None:
    """Create the first admin user if the users table is empty."""
    import_all_models()
    settings = get_settings()
    if not settings.admin_email or not settings.admin_password:
        return
    try:
        db = get_sessionmaker()()
    except RuntimeError as e:
        log.warning("Skipping admin bootstrap: %s", e)
        return
    try:
        existing = db.execute(select(User).limit(1)).scalar_one_or_none()
        if existing is None:
            db.add(User(
                email=settings.admin_email.lower(),
                password_hash=hash_password(settings.admin_password),
                role="admin",
            ))
            db.commit()
            log.info("Bootstrap admin user created: %s", settings.admin_email)
    except Exception:
        log.exception("Admin bootstrap failed (is the database migrated?)")
    finally:
        db.close()


# --- Static frontend ---------------------------------------------------------
# The plain HTML/CSS/JS frontend is served by the backend itself. There is one
# real page (index.html); the JS router swaps view fragments without a reload.
_FRONTEND_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

if os.path.isdir(_FRONTEND_DIR):
    app.mount("/views", StaticFiles(directory=os.path.join(_FRONTEND_DIR, "views")), name="views")
    app.mount("/css", StaticFiles(directory=os.path.join(_FRONTEND_DIR, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(_FRONTEND_DIR, "js")), name="js")
    app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # Any non-API path returns the app shell; the JS router shows the view.
        return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))
