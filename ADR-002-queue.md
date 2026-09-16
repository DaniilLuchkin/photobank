# ADR-002: Database-backed queue with PostgreSQL row locks

Status: Accepted

Use `upload_jobs` with `FOR UPDATE SKIP LOCKED`, leases, retries and persisted attempts. Redis/BullMQ and Celery were rejected for MVP because this is one server and a second queue state would add operational cost. Redis can be added later as a wake-up/cache layer without changing the job model.

