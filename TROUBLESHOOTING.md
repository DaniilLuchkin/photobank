# Troubleshooting

## Container will not start

Check `docker compose config`, `.env` values and `docker compose logs postgres`. The database password in `DATABASE_URL` must match `POSTGRES_PASSWORD` on a fresh volume.

## Files are not discovered

Confirm the host folder is `/home/ubuntu/Stock/inbox`, the file extension is supported, and `docker compose logs -f worker` shows the worker loop. The periodic scan can be forced from the Dashboard with **Scan now**.

## Asset is in NEEDS_REVIEW

This is expected for exact/near duplicates, low AI confidence, failed technical checks or policy/release flags. Review the asset detail page, edit/approve or skip it. The application intentionally does not make legal safety decisions.

## A job is retrying

Inspect worker logs and the job error in the database/UI. Temporary errors use staged backoff; after the retry budget is exhausted the job becomes `FAILED`. Do not blindly retry a provider job after a network timeout unless the adapter has checked remote state.

## Safe restart

`docker compose restart worker` is safe. Claimed jobs have a lease and are requeued after expiry. Provider adapters must retain the idempotency fingerprint and remote state before retrying an interrupted transfer.

