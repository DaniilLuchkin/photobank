# Deployment

1. Use the existing server directory `/home/ubuntu/projects/stock-pipeline`; do not join existing Docker networks.
2. Create `/home/ubuntu/Stock/inbox` and keep the original-media root on persistent storage.
3. Configure `.env` with a random secret, database password and admin password.
4. Start with `docker compose up -d --build`.
5. Verify `docker compose ps`, `/api/health`, worker logs and a small mock-provider fixture.
6. Keep Postgres un-published. The web port is bound to loopback (`127.0.0.1:8910`).
7. If remote access is required, add a route to an existing Caddy/Tailscale setup only after selecting the hostname and authentication boundary. Do not change the existing proxy blindly.

## Resource expectations

The host has 2 vCPU and no swap. Keep worker concurrency at one initially, use remote AI or mock AI, and throttle media/AI jobs. Do not run a heavy local vision model on the host without observing memory and disk usage.

## Backup

Run a daily `pg_dump -Fc` outside the live Postgres volume and periodically test restore. Original media is not included in the MVP database backup; protect `/home/ubuntu/Stock` with the user’s preferred backup tool (restic/rsync/object storage) before relying on it as an archive.

