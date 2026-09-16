# Stock Pipeline

Self-hosted pipeline for local stock photo/video ingestion, duplicate detection, technical analysis, AI metadata and independent provider jobs.

## MVP status

The repository currently contains the working foundation and vertical slice:

`INBOX → PostgreSQL → SHA-256/pHash → image/video analysis → cached canonical metadata → review/quality state → DB queue → mock provider → web UI`

The default AI and provider modes are safe mocks. Shutterstock has an opt-in official FTPS transfer adapter; portal metadata/submission/status remain `MANUAL_REQUIRED`. Pond5, Adobe Stock, Alamy and Storyblocks remain documented adapter targets. No portal browser automation is used.

## Quick start

```bash
cd /home/ubuntu/projects/stock-pipeline
cp .env.example .env
chmod 600 .env
# Set POSTGRES_PASSWORD, DATABASE_URL password and ADMIN_PASSWORD in .env.
mkdir -p /home/ubuntu/Stock/inbox
docker compose up -d --build
```

Open `http://127.0.0.1:8910` locally or expose it through an existing authenticated reverse proxy/Tailscale route after reviewing the security configuration. Put supported files into `/home/ubuntu/Stock/inbox`.

## Supported ingestion formats

Photos: JPG/JPEG, PNG, TIFF, HEIC/HEIF when the decoder is available. Videos: MP4, MOV/M4V, AVI, MKV and WebM when `ffprobe` supports them. The worker scans periodically so a missed filesystem event is reconciled.

## Useful commands

```bash
docker compose ps
docker compose logs -f worker
docker compose logs -f web
docker compose exec postgres pg_isready -U stock -d stock
```

Health: `GET /api/health`. The first configured admin user is created from `ADMIN_USERNAME`/`ADMIN_PASSWORD` on startup.

## Configuration and backup

See [CONFIGURATION.md](CONFIGURATION.md), [DEPLOYMENT.md](DEPLOYMENT.md), [PROVIDERS.md](PROVIDERS.md) and [TROUBLESHOOTING.md](TROUBLESHOOTING.md). A database backup can be made with:

```bash
docker compose exec -T postgres pg_dump -U stock -d stock -Fc > /home/ubuntu/Stock/backups/stock-$(date -u +%Y%m%d).dump
```

Restore into a stopped/disposable database with `pg_restore`; do not overwrite a live database without a verified backup and a maintenance window.

## Adding a provider

Implement `StockProvider` in `app/stock_pipeline/providers/`, register capabilities in `pipeline.seed_providers`, and add provider-specific export/transfer tests. The core pipeline creates one independent provider job per asset/provider and stores separate statuses. If a provider lacks an official contributor API or safe idempotency signal, return `MANUAL_REQUIRED` and export a manifest instead of automating the portal.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DATABASE_URL=sqlite:///./dev.db APP_AUTH_ENABLED=false STOCK_ROOT=./local-stock INBOX_PATH=./local-stock/inbox pytest -q
```

The production Compose database is PostgreSQL. SQLite is used only for fast unit tests/local smoke tests.
