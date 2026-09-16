import hashlib
import logging
import mimetypes
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    AIRequest,
    Asset,
    AssetFile,
    AssetHash,
    AssetSimilarity,
    DailyLimit,
    MediaAnalysis,
    MetadataRecord,
    MetadataVersion,
    PolicyFlag,
    Provider,
    ProviderAsset,
    QualityCheck,
    SystemEvent,
    UploadAttempt,
    UploadJob,
    now,
)
from .providers import MockProvider, ShutterstockFTPSProvider, StockProvider
from .services.ai import build_ai_provider, input_fingerprint
from .services.hashing import hamming_distance, image_perceptual_hash, sha256_file
from .services.media import analyze_media, media_type_for, mime_for
from .services.state import assert_transition

log = logging.getLogger(__name__)


PIPELINE_PROVIDER = "__pipeline__"
PIPELINE_STEPS = {"HASH", "ANALYZE", "METADATA", "QUALITY"}
RETRY_DELAYS = (60, 300, 900, 3600, 21600, 86400)


def event(db: Session, message: str, *, level: str = "INFO", asset_id: str | None = None,
          job_id: str | None = None, provider_id: str | None = None, context: dict[str, Any] | None = None) -> None:
    db.add(SystemEvent(level=level, message=message, asset_id=asset_id, job_id=job_id, provider_id=provider_id, context=context))
    log.log(logging.ERROR if level == "ERROR" else logging.INFO, message, extra={"asset_id": asset_id, "job_id": job_id, "provider": provider_id})


def seed_providers(db: Session, settings: Settings) -> None:
    configured = {
        PIPELINE_PROVIDER: ("Pipeline internal", True, "internal", {"pipeline": True}),
        "mock": ("Mock provider", settings.mock_provider_enabled, "mock", MockProvider().capabilities()),
        "shutterstock": ("Shutterstock", settings.shutterstock_enabled, "manual", {"ftps": True, "photo": True, "video": True}),
        "pond5": ("Pond5", settings.pond5_enabled, "manual", {"ftp": True, "photo": True, "video": True}),
        "adobe": ("Adobe Stock", settings.adobe_enabled, "manual", {"sftp": True, "photo": True, "video": True}),
        "alamy": ("Alamy", settings.alamy_enabled, "manual", {"ftp": True, "photo": True, "video": True}),
        "storyblocks": ("Storyblocks", settings.storyblocks_enabled, "manual", {"sftp": True, "video": True}),
    }
    for provider_id, (name, enabled, mode, capabilities) in configured.items():
        provider = db.get(Provider, provider_id)
        if provider is None:
            db.add(Provider(id=provider_id, name=name, enabled=enabled, mode=mode, capabilities=capabilities, daily_limit=settings.max_assets_per_day))
        else:
            provider.enabled = enabled
            provider.mode = mode
            provider.capabilities = capabilities
    db.commit()


def enqueue(db: Session, asset_id: str, provider_id: str, operation: str, priority: int = 0) -> UploadJob:
    job = UploadJob(asset_id=asset_id, provider_id=provider_id, operation=operation, priority=priority, status="QUEUED")
    db.add(job)
    db.flush()
    return job


def discover_files(db: Session, settings: Settings) -> int:
    settings.ensure_storage()
    created = 0
    supported = []
    for path in settings.inbox_path.rglob("*"):
        if not path.is_file() or media_type_for(path) is None:
            continue
        supported.append(path)
    known_paths = {row[0] for row in db.execute(select(AssetFile.path)).all()}
    for path in supported:
        path_string = str(path.resolve())
        if path_string in known_paths:
            continue
        stat = path.stat()
        asset = Asset(original_filename=path.name, media_type=media_type_for(path), lifecycle_state="DISCOVERED")
        asset.file = AssetFile(path=path_string, file_size=stat.st_size, mime_type=mime_for(path))
        db.add(asset)
        db.flush()
        enqueue(db, asset.id, PIPELINE_PROVIDER, "HASH", _priority_value(asset.priority))
        event(db, f"Discovered {path.name}", asset_id=asset.id)
        created += 1
    if created:
        db.commit()
    return created


def _priority_value(priority: str) -> int:
    return {"HIGH": 20, "NORMAL": 10, "LOW": 0}.get(priority, 10)


def _transition(asset: Asset, target: str) -> None:
    if asset.lifecycle_state == target:
        return
    assert_transition(asset.lifecycle_state, target)
    asset.lifecycle_state = target


def process_hash(db: Session, job: UploadJob) -> None:
    asset = db.get(Asset, job.asset_id)
    assert asset and asset.file
    _transition(asset, "HASHING")
    path = Path(asset.file.path)
    if not path.exists():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    existing = db.scalar(select(AssetHash).where(AssetHash.sha256 == digest, AssetHash.asset_id != asset.id))
    if existing:
        db.add(AssetHash(asset_id=asset.id, sha256=digest))
        _transition(asset, "SKIPPED")
        asset.error = f"Exact duplicate of asset {existing.asset_id}"
        event(db, "Exact duplicate skipped", asset_id=asset.id, context={"duplicate_of": existing.asset_id})
        return
    phash = None
    if asset.media_type == "photo":
        phash = image_perceptual_hash(path)
    db.add(AssetHash(asset_id=asset.id, sha256=digest, perceptual_hash=phash))
    db.flush()
    _transition(asset, "DUPLICATE_CHECKED")
    if phash:
        prior_hashes = db.scalars(select(AssetHash).where(AssetHash.perceptual_hash.is_not(None), AssetHash.asset_id != asset.id)).all()
        for prior in prior_hashes:
            distance = hamming_distance(phash, prior.perceptual_hash)
            if distance <= 8:
                score = max(0.0, 1 - distance / 64)
                db.add(AssetSimilarity(asset_id=asset.id, similar_asset_id=prior.asset_id, score=score, algorithm="phash-v1"))
                _transition(asset, "NEEDS_REVIEW")
                asset.error = f"Near duplicate candidate: {prior.asset_id}"
                event(db, "Near duplicate review required", asset_id=asset.id, context={"similar_asset_id": prior.asset_id, "distance": distance})
                return
    enqueue(db, asset.id, PIPELINE_PROVIDER, "ANALYZE", _priority_value(asset.priority))


def process_analysis(db: Session, job: UploadJob, settings: Settings) -> None:
    asset = db.get(Asset, job.asset_id)
    assert asset and asset.file
    _transition(asset, "ANALYZING")
    analysis, frames = analyze_media(Path(asset.file.path), settings.stock_root / "derivatives", asset.id)
    db.add(MediaAnalysis(asset_id=asset.id, data=analysis, representative_frames=frames))
    asset.file.width = analysis.get("width")
    asset.file.height = analysis.get("height")
    asset.file.duration_seconds = analysis.get("duration_seconds")
    asset.file.fps = analysis.get("fps")
    asset.file.codec = analysis.get("codec")
    _transition(asset, "METADATA_GENERATION")
    enqueue(db, asset.id, PIPELINE_PROVIDER, "METADATA", _priority_value(asset.priority))


def process_metadata(db: Session, job: UploadJob, settings: Settings) -> None:
    asset = db.get(Asset, job.asset_id)
    assert asset and asset.file
    analysis_row = db.scalar(select(MediaAnalysis).where(MediaAnalysis.asset_id == asset.id))
    hash_row = db.scalar(select(AssetHash).where(AssetHash.asset_id == asset.id))
    assert analysis_row and hash_row
    provider = build_ai_provider(settings)
    prompt_version = "metadata-v1"
    fingerprint = input_fingerprint(hash_row.sha256, provider.model, prompt_version)
    cached = db.scalar(select(AIRequest).where(AIRequest.asset_id == asset.id, AIRequest.input_fingerprint == fingerprint, AIRequest.status == "COMPLETED"))
    if cached:
        metadata_version = db.scalar(select(MetadataVersion).join(MetadataRecord).where(MetadataVersion.metadata_id == MetadataRecord.id, MetadataVersion.model == cached.model))
        if metadata_version:
            record = db.scalar(select(MetadataRecord).where(MetadataRecord.asset_id == asset.id))
            record.current_version_id = metadata_version.id
            _transition(asset, "QUALITY_CHECKED")
            enqueue(db, asset.id, PIPELINE_PROVIDER, "QUALITY", _priority_value(asset.priority))
            return
    frames = analysis_row.representative_frames or []
    payload = provider.generate(asset_name=asset.original_filename, media_type=asset.media_type, analysis=analysis_row.data, frames=frames)
    payload["keywords"] = list(dict.fromkeys(str(item).strip().lower() for item in payload.get("keywords", []) if str(item).strip()))[:50]
    payload["confidence"] = float(payload.get("confidence", 0))
    record = db.scalar(select(MetadataRecord).where(MetadataRecord.asset_id == asset.id))
    if not record:
        record = MetadataRecord(asset_id=asset.id)
        db.add(record)
        db.flush()
    version = MetadataVersion(metadata_id=record.id, payload=payload, model=provider.model, prompt_version=prompt_version,
                              confidence=payload["confidence"], estimated_cost_usd=0.0)
    db.add(version)
    db.flush()
    record.current_version_id = version.id
    db.add(AIRequest(asset_id=asset.id, provider=provider.name, model=provider.model, prompt_version=prompt_version,
                     input_fingerprint=fingerprint, status="COMPLETED", cost_usd=0.0, cache_hit=False))
    for flag in payload.get("policy_flags", []):
        db.add(PolicyFlag(asset_id=asset.id, kind=str(flag), confidence=payload["confidence"], evidence={"source": "ai"}))
    _transition(asset, "QUALITY_CHECKED")
    enqueue(db, asset.id, PIPELINE_PROVIDER, "QUALITY", _priority_value(asset.priority))


def process_quality(db: Session, job: UploadJob, settings: Settings) -> None:
    asset = db.get(Asset, job.asset_id)
    assert asset and asset.file
    analysis = db.scalar(select(MediaAnalysis).where(MediaAnalysis.asset_id == asset.id))
    record = db.scalar(select(MetadataRecord).where(MetadataRecord.asset_id == asset.id))
    version = db.get(MetadataVersion, record.current_version_id) if record and record.current_version_id else None
    if not analysis or not version:
        raise ValueError("analysis or metadata missing")
    data = analysis.data
    checks = []
    if asset.media_type == "photo":
        checks.append(("dimensions", "PASS" if data.get("width") and data.get("height") else "FAIL", {"width": data.get("width"), "height": data.get("height")}))
        checks.append(("megapixels", "WARNING" if (data.get("megapixels") or 0) < 4 else "PASS", {"megapixels": data.get("megapixels")}))
        checks.append(("sharpness_proxy", "WARNING" if (data.get("sharpness_proxy") or 0) < 80 else "PASS", {"value": data.get("sharpness_proxy")}))
    else:
        checks.append(("representative_frames", "PASS" if data.get("representative_frame_count", 0) >= 3 else "FAIL", data))
        checks.append(("codec", "WARNING" if not data.get("codec") else "PASS", {"codec": data.get("codec")}))
    for name, result, value in checks:
        db.add(QualityCheck(asset_id=asset.id, name=name, result=result, value=value))
    has_fail = any(result == "FAIL" for _, result, _ in checks)
    review_reasons = [flag.kind for flag in db.scalars(select(PolicyFlag).where(PolicyFlag.asset_id == asset.id, PolicyFlag.resolved.is_(False))).all()]
    if version.confidence < settings.ai_confidence_threshold:
        review_reasons.append("low_ai_confidence")
    if has_fail or review_reasons:
        _transition(asset, "NEEDS_REVIEW")
        asset.error = "; ".join(review_reasons) if review_reasons else "Technical quality check failed"
        event(db, "Asset sent to review", asset_id=asset.id, context={"reasons": review_reasons, "quality_fail": has_fail})
        return
    _transition(asset, "READY")
    if not _reserve_limit(db, "assets_total", settings.max_assets_per_day):
        _transition(asset, "NEEDS_REVIEW")
        asset.error = "Daily asset processing limit reached"
        event(db, "Daily asset limit reached", asset_id=asset.id, level="WARNING")
        return
    enabled = db.scalars(select(Provider).where(Provider.enabled.is_(True), Provider.id != PIPELINE_PROVIDER)).all()
    if not enabled:
        return
    _transition(asset, "QUEUED")
    for provider in enabled:
        idempotency = hashlib.sha256(f"{provider.id}:{asset.id}:{version.id}".encode()).hexdigest()
        existing = db.scalar(select(ProviderAsset).where(ProviderAsset.asset_id == asset.id, ProviderAsset.provider_id == provider.id))
        if not existing:
            db.add(ProviderAsset(asset_id=asset.id, provider_id=provider.id, idempotency_key=idempotency, state="QUEUED"))
            enqueue(db, asset.id, provider.id, "UPLOAD", _priority_value(asset.priority))


def _provider_adapter(provider_id: str, settings: Settings) -> StockProvider:
    if provider_id == "mock":
        return MockProvider()
    if provider_id == "shutterstock" and settings.shutterstock_ftps_user and settings.shutterstock_ftps_password:
        return ShutterstockFTPSProvider(settings.shutterstock_ftps_host, settings.shutterstock_ftps_user, settings.shutterstock_ftps_password, settings.shutterstock_ftps_remote_dir)
    raise NotImplementedError(f"provider adapter {provider_id} is not enabled in MVP")


def _reserve_limit(db: Session, scope: str, limit: int) -> bool:
    day = datetime.now(timezone.utc).date().isoformat()
    row = db.scalar(select(DailyLimit).where(DailyLimit.scope == scope, DailyLimit.day == day).with_for_update())
    if not row:
        row = DailyLimit(scope=scope, day=day, limit=limit)
        db.add(row)
        db.flush()
    if row.used + row.reserved >= row.limit:
        return False
    row.reserved += 1
    return True


def _consume_limit(db: Session, scope: str) -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    row = db.scalar(select(DailyLimit).where(DailyLimit.scope == scope, DailyLimit.day == day).with_for_update())
    if not row or row.reserved <= 0:
        return
    row.reserved = max(0, row.reserved - 1)
    row.used += 1


def process_upload(db: Session, job: UploadJob, settings: Settings) -> None:
    asset = db.get(Asset, job.asset_id)
    provider = db.get(Provider, job.provider_id)
    assert asset and asset.file and provider
    record = db.scalar(select(MetadataRecord).where(MetadataRecord.asset_id == asset.id))
    version = db.get(MetadataVersion, record.current_version_id) if record and record.current_version_id else None
    if not version:
        raise ValueError("metadata missing")
    provider_asset = db.scalar(select(ProviderAsset).where(ProviderAsset.asset_id == asset.id, ProviderAsset.provider_id == provider.id))
    if not provider_asset:
        raise ValueError("provider asset missing")
    if provider_asset.state in {"APPROVED", "MANUAL_REQUIRED", "REJECTED", "FAILED"}:
        job.status = "COMPLETED"
        job.completed_at = now()
        _finish_asset_if_done(db, asset)
        return
    _transition(asset, "UPLOADING") if asset.lifecycle_state == "QUEUED" else None
    if provider.id != "mock" and not (provider.id == "shutterstock" and settings.shutterstock_ftps_user and settings.shutterstock_ftps_password):
        provider_asset.state = "MANUAL_REQUIRED"
        provider_asset.last_error = "Official transfer adapter not configured in this MVP; use provider export/manual flow"
        job.status = "COMPLETED"
        job.completed_at = now()
        event(db, "Provider requires manual upload", asset_id=asset.id, job_id=job.id, provider_id=provider.id, level="WARNING")
        return
    if not _reserve_limit(db, f"provider:{provider.id}", provider.daily_limit):
        job.next_attempt_at = now() + timedelta(minutes=15)
        job.status = "QUEUED"
        return
    if not _reserve_limit(db, "uploads_total", settings.max_uploads_total_per_day):
        provider_limit = db.scalar(select(DailyLimit).where(DailyLimit.scope == f"provider:{provider.id}", DailyLimit.day == datetime.now(timezone.utc).date().isoformat()))
        if provider_limit:
            provider_limit.reserved = max(0, provider_limit.reserved - 1)
        job.next_attempt_at = now() + timedelta(minutes=15)
        job.status = "QUEUED"
        return
    adapter = _provider_adapter(provider.id, settings)
    hash_row = db.scalar(select(AssetHash).where(AssetHash.asset_id == asset.id))
    result = adapter.upload(asset_path=asset.file.path, asset_sha256=hash_row.sha256, metadata=version.payload, idempotency_key=provider_asset.idempotency_key)
    db.add(UploadAttempt(job_id=job.id, attempt_no=job.attempts, status=result.state, result={"message": result.message, "remote_key": result.remote_key}))
    provider_asset.state = result.state
    provider_asset.remote_key = result.remote_key
    if result.state == "UPLOADED":
        _consume_limit(db, f"provider:{provider.id}")
        _consume_limit(db, "uploads_total")
        _consume_limit(db, "assets_total")
        submitted = adapter.submit_metadata(remote_key=result.remote_key or "", metadata=version.payload)
        provider_asset.state = submitted.state
        provider_asset.remote_key = submitted.remote_key or provider_asset.remote_key
    job.status = "COMPLETED"
    job.completed_at = now()
    event(db, "Provider upload completed", asset_id=asset.id, job_id=job.id, provider_id=provider.id)
    _finish_asset_if_done(db, asset)


def _finish_asset_if_done(db: Session, asset: Asset) -> None:
    states = db.scalars(select(ProviderAsset).where(ProviderAsset.asset_id == asset.id)).all()
    if states and all(item.state in {"APPROVED", "MANUAL_REQUIRED", "REJECTED", "FAILED"} for item in states):
        if any(item.state == "APPROVED" for item in states):
            if asset.lifecycle_state == "UPLOADING":
                _transition(asset, "SUBMITTED")
            if asset.lifecycle_state == "SUBMITTED":
                _transition(asset, "APPROVED")
        elif any(item.state == "MANUAL_REQUIRED" for item in states):
            if asset.lifecycle_state == "UPLOADING":
                _transition(asset, "MANUAL_REQUIRED")


def retry_delay(attempts: int) -> int:
    return RETRY_DELAYS[min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)]
