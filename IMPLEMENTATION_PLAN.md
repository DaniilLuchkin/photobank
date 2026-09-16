# Stock Pipeline — implementation plan

Дата reconnaissance: 2026-09-16

## 1. Executive decision

Проект будет размещён в `/home/ubuntu/projects/stock-pipeline` и не будет изменять существующие Compose-проекты. Управляемое хранилище по умолчанию — `/home/ubuntu/Stock`:

```text
/home/ubuntu/Stock/
├── inbox/                 # единственная папка, куда пользователь кладёт новые файлы
├── library/               # оригиналы, не удаляются автоматически
├── derivatives/           # thumbnails, poster frames, representative frames
├── releases/              # загруженные model/property releases
├── exports/               # provider-specific CSV/IPTC/transfer manifests
├── backups/               # локальные DB dumps, если включены
└── logs/
```

Database является source of truth. Файлы не перемещаются между смысловыми каталогами после регистрации без явной операции, а связи хранятся через `asset_id`, абсолютный/контейнерный путь, размер и SHA-256.

Первая deployment strategy:

- один Compose project: `web`, `worker`, `postgres`;
- PostgreSQL и worker-сеть не публикуются наружу;
- web временно публикуется только как `127.0.0.1:8910->8000`, чтобы не конфликтовать с существующими портами;
- TLS и внешний hostname не добавляются автоматически: существующие Caddy/Tailscale остаются нетронутыми, а reverse-proxy route добавляется отдельным осознанным изменением;
- Redis не нужен для MVP: очередь будет надёжной database-backed очередью PostgreSQL с `FOR UPDATE SKIP LOCKED`. Redis можно добавить позднее для кеша/сигналов, не меняя доменную модель.

## 2. Environment findings

Сервер: Ubuntu 24.04.4 LTS, ARM64, 2 vCPU, 11 GiB RAM, swap отсутствует. На root volume 193 GiB, свободно примерно 162 GiB.

Доступны Docker 29.7.2, Docker Compose v5.4.0, Node.js 24.20.0/npm 11.19.0 и Python 3.12.3/pip 24.0. На host отсутствуют FFmpeg, ImageMagick и ExifTool; они будут установлены только в application image.

Уже занятые host-порты: 22, 80, 443, 3000, 8000, 2053, 2083, 4173, а также Tailscale listeners 443, 8899 и 39448. Уже работают Compose-проекты `forwardlyai`, `mtg`, `noema`, `openwebui` и `simple-cards-prod`. Новый проект не должен подключаться к их сетям или базам данных.

Tailscale активен, адрес сервера `100.110.245.82`. Системный/контейнерный Caddy уже используется существующими приложениями, но его конфигурация не будет изменяться на этапе MVP.

## 3. Technology choice

### Выбранный вариант: Python monolith + отдельный Python worker

| Критерий | Node/TypeScript | Python | Решение |
|---|---|---|---|
| Filesystem/media processing | хороший, но больше native bindings | зрелый стек `ffmpeg`, Pillow, OpenCV, imagehash, ExifTool | Python |
| AI/vision и structured output | хороший | наиболее прямой путь к CV и HTTP SDK | Python |
| PostgreSQL | отличный | SQLAlchemy/psycopg зрелые | Python |
| Queue/long-running worker | BullMQ требует Redis | PostgreSQL queue достаточна для одного сервера | Python |
| UI | React/Vite сильнее | Jinja2 + HTMX достаточно для functional UI | Python server-rendered |
| Обслуживание | два ecosystem-а для media и backend | один ecosystem и два процесса | Python |

Стек MVP:

- Python 3.12;
- FastAPI, Jinja2, HTMX, минимальный CSS;
- SQLAlchemy 2 + Alembic + psycopg;
- PostgreSQL 16;
- Pydantic v2 для API и canonical metadata;
- `watchdog` для событий и periodic scan как safety net;
- Pillow + `imagehash` + OpenCV для изображений;
- FFmpeg/ffprobe для видео и representative frames;
- ExifTool для EXIF/IPTC/XMP/GPS, если нужен более полный extraction;
- `argon2-cffi` для password hashing;
- структурированный JSON logging;
- pytest, pytest-asyncio, testcontainers/Compose integration tests.

Одна web-служба и один worker — отдельные containers, но общий application package. В MVP не будет Kubernetes, микросервисов, Kafka или browser automation.

## 4. Logical architecture

```text
INBOX
  -> discovery (watcher + periodic scan)
  -> asset registration + exact hash
  -> image/video analysis + derivatives
  -> near-duplicate candidate groups
  -> AI metadata (cached, versioned)
  -> deterministic quality/policy checks
  -> READY or NEEDS_REVIEW
  -> provider upload jobs (one independent job per provider)
  -> provider-specific submission/status/manual action
  -> dashboard, audit log, optional Telegram summary
```

Ключевые границы:

- core pipeline знает только `StockProvider` и canonical metadata;
- provider adapter отвечает за capability validation, transport, idempotency hints и mapping статусов;
- AI может описывать/flag-ить, но не принимает юридическое решение;
- любое неуверенное действие заканчивается `NEEDS_REVIEW` или `MANUAL_REQUIRED`;
- failure одного provider не блокирует jobs других providers.

## 5. State machine

Asset states:

```text
DISCOVERED -> HASHING -> DUPLICATE_CHECKED -> ANALYZING
  -> METADATA_GENERATION -> QUALITY_CHECKED -> READY -> QUEUED
  -> UPLOADING -> SUBMITTED -> PROCESSING -> APPROVED/REJECTED

Любое безопасно неразрешимое состояние -> NEEDS_REVIEW
Provider-specific unsupported automation -> MANUAL_REQUIRED
User decision -> SKIPPED
Непоправимая техническая ошибка -> FAILED
```

Переходы валидируются в domain service и пишутся в `system_events`. Provider asset state хранится отдельно, поэтому один provider может быть `MANUAL_REQUIRED`, а другой `APPROVED` у одного asset.

## 6. Database schema

Нормализованные основные таблицы:

- `assets`: UUID, media type, lifecycle state, priority, original filename, timestamps;
- `asset_files`: path, size, MIME, dimensions, duration, FPS, codec, audio, file role;
- `asset_hashes`: SHA-256, perceptual hashes, video frame hashes, unique constraints;
- `asset_similarity`: asset pairs/groups, score, algorithm/version, review decision;
- `media_analysis`: technical extraction, EXIF/IPTC/GPS and representative-frame manifest;
- `metadata`: current canonical metadata pointer per asset;
- `metadata_versions`: immutable canonical JSON, model, prompt version, generated time, confidence, estimated/actual cost;
- `providers`: stable id, enabled, capabilities snapshot, limits, credential reference/status;
- `provider_assets`: per-provider remote key, lifecycle state, last remote status, idempotency fingerprint, manual reason;
- `upload_jobs`: asset/provider/action, status, priority, attempts, retry timestamps, lease, error;
- `upload_attempts`: every attempt, request fingerprint, result, redacted error, timestamps;
- `quality_checks`: check name, PASS/WARNING/FAIL, value, threshold version, evidence;
- `policy_flags`: face/logo/trademark/location/sensitive/release flags and confidence;
- `releases`: model/property file, provider mappings, metadata, attachment status;
- `ai_requests`: provider/model/prompt version, input fingerprint, tokens, cost, cache hit, status;
- `daily_limits`: scope/date/limit/used/reserved;
- `system_events`: structured audit trail;
- `users`, `sessions`, `csrf_tokens` (or signed session store) for authentication.

Important constraints:

- unique `assets.sha256` for exact duplicate identity;
- unique `(asset_id, provider_id, operation)` or deterministic idempotency key for provider jobs;
- unique `(asset_id, metadata_version)` semantics via immutable versions;
- row-level reservation transaction for daily limits;
- no secrets in logs or event payloads.

## 7. Queue design

`upload_jobs` is a durable PostgreSQL queue. Worker claims jobs in a transaction:

```sql
SELECT id
FROM upload_jobs
WHERE status = 'QUEUED'
  AND next_attempt_at <= now()
ORDER BY priority DESC, created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

The worker sets a lease/`started_at`, performs the action, and commits a terminal/retry state. A watchdog requeues expired leases after restart. Retry delays are 1m, 5m, 15m, 1h, 6h, 24h with jitter and a configurable maximum. Permanent validation, authentication, unsupported-operation and invalid-file errors become `PERMANENT_FAILURE` or `MANUAL_REQUIRED`.

Daily limits are reserved atomically before provider upload. There are separate counters for unique processed assets, total uploads and each provider. A provider may have a smaller configured/account limit; the adapter must not invent a higher limit than documented or configured by the user.

## 8. Canonical metadata and AI

Canonical object:

```json
{
  "title": "",
  "description": "",
  "keywords": [],
  "categories": [],
  "location": {"value": null, "source": null, "confidence": 0},
  "content_type": "commercial",
  "editorial_reason": null,
  "release_hints": [],
  "policy_flags": [],
  "confidence": 0,
  "warnings": []
}
```

Prompt files are versioned under `prompts/` (`image-analysis-v1`, `video-analysis-v1`, `metadata-v1`, `keyword-v1`). Every `metadata_versions` and `ai_requests` row stores `model`, `prompt_version`, `metadata_version`, input fingerprint and generated time.

`AIProvider` implementations:

- OpenAI vision/structured output;
- OpenRouter vision/structured output;
- generic local OpenAI-compatible endpoint as a local-model option.

AI input for video is thumbnail plus configurable representative frames: 3 for short, 5 for medium, 8–12 for long clips. Results are cached by media fingerprint + prompt/model/version. Low confidence, missing location evidence, faces, possible logos/trademarks, sensitive locations and release hints create review flags; the system never says that content is legally safe.

## 9. Provider capability matrix

This matrix distinguishes official transfer capability from full automatic submission. A transfer method is not treated as permission to automate portal clicks or to bypass Terms of Service.

| Provider | Official upload API | FTP/SFTP | CSV metadata | Browser automation | Photo | Video | Metadata limits / notes | Categories | Editorial | Releases | MVP status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Shutterstock | No public contributor upload API found in official docs | FTPS `ftps.shutterstock.com:21` | Yes, in Submit page; not via FTPS | Portal is supported for humans; do not automate clicks | Yes | Yes | 7–50 keywords, category required; filename restrictions and content-specific limits | 1 required, second optional in current help | Yes | Model/property at submission stage | Implement FTPS upload + manifest/CSV export; submission/status `MANUAL_REQUIRED` until official contributor API exists |
| Pond5 | No public contributor upload API found | FTP `ftp.pond5.com` documented; secure mode must be confirmed per account | Apply CSV in portal; required Originalfilename/Title/Keywords, release/editorial fields | No automation implemented | Yes | Yes | Footage title guidance 40–80 chars; keywords required; CSV is portal operation | Not established in reviewed docs | Yes | `Modelreleased`, `Propertyreleased`, `Release` CSV fields | Implement FTP upload + CSV export; portal submit `MANUAL_REQUIRED` |
| Adobe Stock | Explicitly no Stock Contributors API | SFTP for qualified accounts | CSV portal, max 5,000 rows/1 MB; filename 30, title 70, max 50 keywords | No automation implemented | JPEG, min 4MP | MOV/MP4 H.264 | Exact CSV headers; release filename column; qualified SFTP account required | Numeric category required | Illustrative editorial rules | Model/property release, PDF up to 10MB per reviewed docs | Implement SFTP upload + CSV export; submit/status `MANUAL_REQUIRED` |
| Alamy | No public contributor upload API found | FTP documented | No public bulk CSV contract found; IPTC caption/keywords are imported | No automation implemented | Yes | Yes | At least 5 tags in guide; location/date/license metadata; first QC test | License type rather than fixed category matrix | Editorial-only option documented | Commercial recognizable people/property generally require releases | Implement FTP + embedded IPTC; status/submission `MANUAL_REQUIRED` |
| Storyblocks | No public contributor upload API found | sFTP `contributor-upload.storyblocks.com` | CSV available in contributor portal | No automation implemented | Not established as a core supported type | Yes | Minimum 6 keywords; title at least 16 chars; >5,000 uploads require contact; releases not via FTP | Not established in reviewed docs | Yes | Releases must be uploaded/attached in portal | Video adapter: sFTP + CSV export; portal/release steps `MANUAL_REQUIRED` |

### Provider research conclusions

1. Shutterstock, Pond5 and Adobe all provide an official bulk transfer route, but their reviewed official materials still require the contributor portal for metadata application and/or final submission.
2. Adobe explicitly states that there is no Stock Contributors API and contributor use cases are not approved for its general Stock API.
3. Alamy and Storyblocks are suitable additional adapters because they document FTP/sFTP contributor upload. Storyblocks is primarily a footage adapter in this design.
4. No browser automation will be shipped. It would create a fragile login/session mechanism and could violate provider rules; unsupported steps are represented as `MANUAL_REQUIRED` with an export artifact and clear UI instructions.
5. Provider documentation and limits can change. The matrix is a dated design input, not a permanent legal guarantee; every real adapter has a capability/health check and an explicit disabled/manual fallback.

Official sources reviewed:

- Shutterstock upload and FTPS: <https://submit.shutterstock.com/help/en/articles/12136175-how-do-i-submit-content-to-shutterstock>, <https://submit.shutterstock.com/help/en/articles/10617392-how-do-i-upload-content-via-ftps>, <https://submit.shutterstock.com/help/en/articles/10617486-how-do-i-include-existing-metadata-with-your-content-submission>
- Pond5 upload and CSV metadata: <https://contributor.pond5.com/getting-started/uploading-your-files/>, <https://contributor.pond5.com/getting-started/preparing-your-files/>
- Adobe contributor API FAQ, SFTP, CSV and formats: <https://developer.adobe.com/stock/docs/faq/>, <https://helpx.adobe.com/stock/contributor/submit-your-content/submit-videos/submit-videos.html>, <https://helpx.adobe.com/stock/contributor/manage-your-portfolio/csv-requirements-content.html>, <https://helpx.adobe.com/stock/contributor/content-policies-guidelines/content-policies/content-upload-guidelines.html>
- Alamy upload/metadata/terms: <https://www.alamy.com/contributor/>, <https://www.alamy.com/help/contributor-image-sales-guide/>, <https://www.alamy.com/help/contributor-image-management/>, <https://www.alamy.com/terms/contributor.aspx>
- Storyblocks sFTP, CSV and releases: <https://contribute.storyblocks.com/getting-started>, <https://contribute-faq.storyblocks.com/en/articles/4543900-how-do-i-upload-video-clips>, <https://contribute-faq.storyblocks.com/en/articles/4543705-what-releases-are-needed>

## 10. Idempotency

Each provider operation has a deterministic fingerprint based on `provider_id`, asset SHA-256, canonical metadata version and operation. Before retrying an interrupted upload, the adapter checks `provider_assets` and provider-visible state when the official transfer mechanism exposes it. If the provider cannot return a reliable remote key/status, the job stops in `MANUAL_REQUIRED` instead of blindly resending.

FTP/SFTP is treated as a transfer, not proof of submission. The adapter records remote filename, transfer completion, checksum where available and a manifest. Final portal submit remains a separate action. This prevents a network timeout from creating an uncontrolled second submission.

## 11. Filesystem ingestion and media analysis

The watcher handles `JPG`, `JPEG`, `PNG`, `TIFF`, `HEIC` where decoder support exists, `MP4`, `MOV`, and additional formats accepted by ffprobe. Files are debounced until size/mtime is stable. A scan every 5 minutes reconciles missed filesystem events.

Image checks: MIME/signature, dimensions, megapixels, aspect ratio, color profile, EXIF/IPTC/GPS, corruption, sharpness proxy, noise/exposure warnings and perceptual hash.

Video checks: container/codec, resolution, FPS, bitrate, duration, audio, `ffprobe` errors, sampled black/corrupt frames, blur/artifact warnings, representative-frame hashes and poster frame.

Checks emit `PASS`, `WARNING` or `FAIL`; provider-specific hard requirements are applied only in provider validation, not as global invented quality rules.

Near duplicates are never deleted. Image pHash/dHash plus optional embedding comparison creates review groups. Video groups use representative-frame hashes and duration/resolution similarity. UI offers keep, skip or upload separately.

## 12. Security model

- Argon2id password hashing; no default production password.
- HttpOnly, Secure (when behind HTTPS), SameSite session cookie; short idle/absolute expiry.
- CSRF token on state-changing form/API requests; same-origin policy and rate-limited login.
- API keys and FTP/SFTP credentials stay server-side, encrypted at rest where practical, and are never returned to frontend after save.
- Postgres has no host port; worker and web use a private Compose network.
- Containers run with non-root user where compatible, read-only filesystem where possible, dropped Linux capabilities, no Docker socket.
- Upload paths are resolved under configured storage root; symlinks/path traversal rejected.
- Structured logs redact secrets, tokens, passwords and release PII where possible.
- Initial access is local/Tailscale/reverse-proxy controlled; the app still requires authentication.

## 13. Backup and recovery

MVP includes a documented `pg_dump --format=custom` backup command and a daily host/systemd or operator-triggered job writing outside the live Postgres volume. Restore is tested against a disposable Compose database. Original media backup is intentionally out of scope for MVP but storage layout remains compatible with rsync/restic/object storage.

## 14. Implementation phases

### Phase 1 — foundation

Compose, `.env.example`, app configuration, migrations, health endpoint, structured logging, auth skeleton and storage directories.

### Phase 2 — vertical pipeline

Watcher/scan, asset registration, SHA-256, duplicate state, image/video technical analysis, thumbnails/frames, quality checks, canonical metadata and mock AI provider.

### Phase 3 — queue and mock provider

Database queue, leases, retries, daily limits, idempotency fingerprints, mock stock provider and per-provider statuses.

### Phase 4 — functional UI

Dashboard, asset list/detail, editable metadata, needs-review actions, jobs/errors and provider settings. No SPA requirement.

### Phase 5 — real transfer adapters

Shutterstock FTPS, Pond5 FTP, Adobe SFTP, Alamy FTP/IPTC and Storyblocks sFTP. Each adapter initially exports the required metadata artifact and stops before unsupported portal submission.

### Phase 6 — AI providers and operations

OpenAI/OpenRouter/local-compatible adapters, cost accounting, prompt versioning, optional Telegram critical/daily summary, backup automation and production hardening.

### Phase 7 — tests and release

Unit tests, Compose integration tests, E2E fixture suite, failure/restart/idempotency tests, docs and deployment checklist.

## 15. Estimated complexity

- MVP vertical slice: medium/high, approximately 8–12 focused engineering days.
- Production hardening and functional UI: approximately 5–8 days.
- Each FTP/SFTP adapter and provider-specific export: approximately 1–3 days, excluding account verification and manual portal tests.
- Real automated submission/status integration: unknown until a provider publishes/approves an official contributor API; it is intentionally not estimated as browser automation.

## 16. Known limitations

- AI vision quality and legal flags are advisory only.
- Near-duplicate thresholds require tuning on the user’s archive.
- Face/logo detection may use provider AI or local models later; MVP can flag only available signals and must not claim completeness.
- Stock sites can change limits, portals, credentials and Terms of Service.
- Without a provider contributor API, upload transfer and final submission/status cannot be represented as one fully automated transaction.
- No automatic deletion of originals and no automatic release creation.
- Two vCPU/no swap is enough for a throttled pipeline but not for heavy local embedding models; local model support is endpoint-based and optional.

## 17. Implementation status after vertical slice

Implemented and smoke-tested in Docker Compose:

- PostgreSQL-backed asset and job model with compatibility bootstrap for the current schema;
- filesystem watcher plus periodic reconciliation scan;
- SHA-256 exact duplicates and image pHash near-duplicate review;
- Pillow image inspection, ffprobe/FFmpeg video inspection and representative frames;
- mock/OpenAI-compatible AI abstraction with fingerprint cache fields and versioned prompts;
- deterministic quality/review routing, policy flags and model/property release placeholders;
- independent provider jobs, retry backoff, daily reservations and mock provider idempotency;
- FastAPI dashboard, asset browser/detail page, CSRF-protected actions and editable metadata;
- opt-in Shutterstock FTPS transfer adapter with conservative ambiguous-transfer handling;
- Compose deployment with loopback-only web port and private PostgreSQL.

Remaining before calling the system fully production-ready: replace bootstrap schema creation with a complete Alembic migration chain, implement provider-specific CSV/IPTC/SFTP adapters after account-level verification, add Telegram/statistics pages, add lease recovery and expanded integration/E2E tests, and perform a real Shutterstock transfer only with the owner’s contributor credentials and a maintenance window.

## 18. Definition of MVP completion

The first release is complete when Docker Compose starts web/worker/PostgreSQL, INBOX discovery is reconciled, exact and near duplicates are represented, image and video inspection works, canonical metadata is cached, review/quality states are visible, database queue retries and limits work, mock provider is idempotent, UI supports core actions, secrets stay out of git, and tests cover restart/duplicate/provider isolation. Real provider adapters are shipped as explicitly documented transfer/manual adapters; no unsupported portal automation is used.
