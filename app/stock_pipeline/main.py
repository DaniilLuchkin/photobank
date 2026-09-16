import secrets
from datetime import timedelta
from pathlib import Path

from argon2 import PasswordHasher
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .models import Asset, AssetFile, AssetHash, MetadataRecord, MetadataVersion, Provider, ProviderAsset, QualityCheck, Session as UserSession, SystemEvent, UploadJob, User, now
from .pipeline import PIPELINE_PROVIDER, discover_files, enqueue, seed_providers
from .services.media import media_type_for

settings = get_settings()
app = FastAPI(title="Stock Pipeline", version="0.1.0")
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, max_age=60 * 60 * 12, same_site="lax", https_only=settings.app_env == "production")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
password_hasher = PasswordHasher()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


templates.env.globals["csrf_token"] = csrf_token


@app.on_event("startup")
def startup() -> None:
    init_db()
    with SessionLocal() as db:
        seed_providers(db, settings)
        if settings.admin_password:
            user = db.scalar(select(User).where(User.username == settings.admin_username))
            if not user:
                db.add(User(username=settings.admin_username, password_hash=password_hasher.hash(settings.admin_password)))
                db.commit()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    if not settings.app_auth_enabled:
        return User(id="local", username="local", password_hash="", is_active=True)
    session_id = request.cookies.get("stock_session")
    if not session_id:
        return None
    row = db.get(UserSession, session_id)
    if not row or row.expires_at < now():
        return None
    user = db.get(User, row.user_id)
    return user if user and user.is_active else None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def stats(db: Session) -> dict[str, int]:
    rows = db.execute(select(Asset.lifecycle_state, func.count(Asset.id)).group_by(Asset.lifecycle_state)).all()
    result = {state.lower(): count for state, count in rows}
    result["total"] = sum(result.values())
    result["inbox"] = result.get("discovered", 0) + result.get("hashing", 0) + result.get("analyzing", 0)
    result["processing"] = result.get("uploading", 0) + result.get("queued", 0) + result.get("submitted", 0)
    result["needs_review"] = result.get("needs_review", 0) + result.get("manual_required", 0)
    return result


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/stats")
def api_stats(_: User = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, object]:
    return {"assets": stats(db), "providers": [{"id": item.id, "name": item.name, "enabled": item.enabled, "mode": item.mode} for item in db.scalars(select(Provider)).all()]}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form(""), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    user = db.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
    try:
        valid = bool(user and password_hasher.verify(user.password_hash, password))
    except Exception:  # noqa: BLE001
        valid = False
    if not valid:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    session_id = secrets.token_urlsafe(32)
    db.add(UserSession(id=session_id, user_id=user.id, expires_at=now() + timedelta(hours=12)))
    db.commit()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("stock_session", session_id, httponly=True, secure=settings.app_env == "production", samesite="lax", max_age=60 * 60 * 12)
    return response


@app.post("/logout")
def logout(request: Request, csrf: str = Form(""), _: User = Depends(require_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    session_id = request.cookies.get("stock_session")
    if session_id:
        row = db.get(UserSession, session_id)
        if row:
            db.delete(row)
            db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("stock_session")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _: User = Depends(require_user), db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats(db), "providers": db.scalars(select(Provider).order_by(Provider.id)).all()})


@app.get("/assets", response_class=HTMLResponse)
def assets_page(request: Request, state: str | None = None, media: str | None = None, _: User = Depends(require_user), db: Session = Depends(get_db)):
    query = select(Asset).order_by(Asset.created_at.desc())
    if state:
        query = query.where(Asset.lifecycle_state == state.upper())
    if media:
        query = query.where(Asset.media_type == media)
    assets = db.scalars(query.limit(200)).all()
    return templates.TemplateResponse("assets.html", {"request": request, "assets": assets, "state": state or "", "media": media or ""})


@app.get("/assets/{asset_id}", response_class=HTMLResponse)
def asset_detail(request: Request, asset_id: str, _: User = Depends(require_user), db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    metadata = db.scalar(select(MetadataRecord).where(MetadataRecord.asset_id == asset.id))
    version = db.get(MetadataVersion, metadata.current_version_id) if metadata and metadata.current_version_id else None
    providers = db.execute(select(Provider, ProviderAsset).join(ProviderAsset, Provider.id == ProviderAsset.provider_id, isouter=True).where(ProviderAsset.asset_id == asset.id)).all()
    quality = db.scalars(select(QualityCheck).where(QualityCheck.asset_id == asset.id).order_by(QualityCheck.created_at.desc())).all()
    return templates.TemplateResponse("asset_detail.html", {"request": request, "asset": asset, "version": version, "providers": providers, "quality": quality})


@app.post("/assets/{asset_id}/approve")
def approve_asset(request: Request, asset_id: str, csrf: str = Form(""), _: User = Depends(require_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404)
    if asset.lifecycle_state not in {"NEEDS_REVIEW", "MANUAL_REQUIRED", "FAILED"}:
        raise HTTPException(409, f"Cannot approve from {asset.lifecycle_state}")
    asset.error = None
    asset.lifecycle_state = "READY"
    asset.lifecycle_state = "QUEUED"
    providers = db.scalars(select(Provider).where(Provider.enabled.is_(True), Provider.id != PIPELINE_PROVIDER)).all()
    version = db.scalar(select(MetadataVersion).join(MetadataRecord).where(MetadataRecord.asset_id == asset.id).order_by(MetadataVersion.generated_at.desc()))
    for provider in providers:
        existing = db.scalar(select(ProviderAsset).where(ProviderAsset.asset_id == asset.id, ProviderAsset.provider_id == provider.id))
        if not existing and version:
            key = secrets.token_hex(32)
            db.add(ProviderAsset(asset_id=asset.id, provider_id=provider.id, idempotency_key=key, state="QUEUED"))
            enqueue(db, asset.id, provider.id, "UPLOAD", 20 if asset.priority == "HIGH" else 10)
    db.commit()
    return RedirectResponse(f"/assets/{asset_id}", status_code=303)


@app.post("/assets/{asset_id}/skip")
def skip_asset(request: Request, asset_id: str, csrf: str = Form(""), _: User = Depends(require_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404)
    asset.lifecycle_state = "SKIPPED"
    db.commit()
    return RedirectResponse("/assets", status_code=303)


@app.post("/assets/{asset_id}/retry")
def retry_asset(request: Request, asset_id: str, csrf: str = Form(""), _: User = Depends(require_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404)
    asset.lifecycle_state = "READY"
    asset.error = None
    db.add(SystemEvent(message="Retry requested from UI", asset_id=asset.id))
    db.commit()
    return RedirectResponse(f"/assets/{asset_id}", status_code=303)


@app.post("/assets/{asset_id}/metadata")
def edit_metadata(request: Request, asset_id: str, title: str = Form(...), description: str = Form(...), keywords: str = Form(""), csrf: str = Form(""), _: User = Depends(require_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404)
    record = db.scalar(select(MetadataRecord).where(MetadataRecord.asset_id == asset.id))
    current = db.get(MetadataVersion, record.current_version_id) if record and record.current_version_id else None
    if not record or not current:
        raise HTTPException(409, "Metadata is not available yet")
    payload = dict(current.payload)
    payload.update({"title": title.strip(), "description": description.strip(), "keywords": list(dict.fromkeys(item.strip().lower() for item in keywords.split(",") if item.strip()))[:50]})
    version = MetadataVersion(metadata_id=record.id, payload=payload, model="manual", prompt_version="manual-v1", confidence=current.confidence, estimated_cost_usd=0.0)
    db.add(version)
    db.flush()
    record.current_version_id = version.id
    db.add(SystemEvent(message="Metadata edited from UI", asset_id=asset.id))
    db.commit()
    return RedirectResponse(f"/assets/{asset_id}", status_code=303)


@app.post("/scan")
def scan(request: Request, csrf: str = Form(""), _: User = Depends(require_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    created = discover_files(db, settings)
    return RedirectResponse(f"/?scanned={created}", status_code=303)
