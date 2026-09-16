# Configuration

Copy `.env.example` to `.env`; `.env` is ignored by git and must not be committed.

Required before production:

- `POSTGRES_PASSWORD` and the matching password in `DATABASE_URL`;
- long random `APP_SECRET_KEY`;
- unique strong `ADMIN_PASSWORD`;
- `STOCK_ROOT_HOST` pointing to the original-media storage location;
- `APP_AUTH_ENABLED=true`.

Pipeline controls:

- `MAX_ASSETS_PER_DAY`: unique assets allowed through processing;
- `MAX_UPLOADS_TOTAL_PER_DAY`: total provider uploads;
- `SCAN_INTERVAL_SECONDS`: reconciliation scan interval;
- `WORKER_POLL_SECONDS`: queue poll interval;
- `AI_PROVIDER=mock|openai|openrouter`;
- `AI_MODEL`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, optional `AI_BASE_URL`;
- `AI_CONFIDENCE_THRESHOLD`, `AI_DAILY_BUDGET_USD`.

Provider enable flags default to false. Enabling a provider in the MVP registers its capability and creates a manual-required job unless its adapter is implemented. Secrets are server-only and are never rendered in the UI.

Shutterstock has an official FTPS transfer adapter. Set `SHUTTERSTOCK_ENABLED=true` and its FTPS credentials only after verifying the contributor account settings. The adapter uploads the file, then deliberately returns `MANUAL_REQUIRED` for portal CSV/release metadata and review status. An interrupted/ambiguous FTPS transfer is also stopped for manual verification rather than retried blindly.
