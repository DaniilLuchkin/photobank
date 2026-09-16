import logging
import threading
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import get_settings
from .db import SessionLocal, init_db
from .models import UploadJob, now
from .pipeline import (
    PIPELINE_PROVIDER,
    discover_files,
    process_analysis,
    process_hash,
    process_metadata,
    process_quality,
    process_upload,
    retry_delay,
    seed_providers,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


class InboxChangeHandler(FileSystemEventHandler):
    """Wake the worker quickly while periodic scans remain the safety net."""

    def __init__(self) -> None:
        self.changed = threading.Event()

    def on_created(self, event) -> None:  # noqa: ANN001 - watchdog event type
        if not event.is_directory:
            self.changed.set()

    def on_moved(self, event) -> None:  # noqa: ANN001 - watchdog event type
        if not event.is_directory:
            self.changed.set()


def claim_job(db):
    job = db.scalar(
        select(UploadJob)
        .where(UploadJob.status == "QUEUED", UploadJob.next_attempt_at <= now())
        .order_by(UploadJob.priority.desc(), UploadJob.created_at)
        .with_for_update(skip_locked=True)
    )
    if not job:
        return None
    job.status = "RUNNING"
    job.attempts += 1
    job.started_at = now()
    job.lease_until = now() + timedelta(minutes=15)
    db.commit()
    return job.id


def recover_expired_jobs(db) -> int:
    jobs = db.scalars(select(UploadJob).where(UploadJob.status == "RUNNING", UploadJob.lease_until.is_not(None), UploadJob.lease_until < now())).all()
    for job in jobs:
        job.status = "QUEUED"
        job.next_attempt_at = now()
        job.lease_until = None
        job.error = "Worker lease expired; safely requeued"
    if jobs:
        db.commit()
    return len(jobs)


def run_job(job_id: str, settings) -> None:
    db = SessionLocal()
    job = db.get(UploadJob, job_id)
    if not job:
        db.close()
        return
    try:
        if job.operation == "HASH":
            process_hash(db, job)
        elif job.operation == "ANALYZE":
            process_analysis(db, job, settings)
        elif job.operation == "METADATA":
            process_metadata(db, job, settings)
        elif job.operation == "QUALITY":
            process_quality(db, job, settings)
        elif job.operation == "UPLOAD":
            process_upload(db, job, settings)
        else:
            raise ValueError(f"unknown operation {job.operation}")
        if job.status == "RUNNING":
            job.status = "COMPLETED"
            job.completed_at = now()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - job boundary must persist failure
        db.rollback()
        job = db.get(UploadJob, job_id)
        if job:
            job.error = str(exc)[:2000]
            if job.attempts >= 6:
                job.status = "FAILED"
            else:
                job.status = "QUEUED"
                job.next_attempt_at = now() + timedelta(seconds=retry_delay(job.attempts))
            db.commit()
        log.exception("job failed id=%s", job_id)
    finally:
        db.close()


def main() -> None:
    settings = get_settings()
    init_db()
    settings.ensure_storage()
    changes = InboxChangeHandler()
    observer = Observer()
    observer.schedule(changes, str(Path(settings.inbox_path)), recursive=True)
    observer.start()
    last_scan = 0.0
    with SessionLocal() as db:
        seed_providers(db, settings)
    try:
        while True:
            now_monotonic = time.monotonic()
            if changes.changed.is_set() or now_monotonic - last_scan >= settings.scan_interval_seconds:
                with SessionLocal() as db:
                    discover_files(db, settings)
                changes.changed.clear()
                last_scan = now_monotonic
            with SessionLocal() as db:
                recover_expired_jobs(db)
                job_id = claim_job(db)
            if job_id:
                run_job(job_id, settings)
            else:
                changes.changed.wait(timeout=settings.worker_poll_seconds)
                changes.changed.clear()
    finally:
        observer.stop()
        observer.join(timeout=5)


if __name__ == "__main__":
    main()
