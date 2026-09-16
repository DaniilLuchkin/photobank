# ADR-001: PostgreSQL as source of truth

Status: Accepted

Use PostgreSQL in the isolated Compose project. SQLite is acceptable only for unit tests/local smoke tests. The system needs relational constraints, durable queue claims, provider-specific status, metadata versions and daily-limit reservations. A shared existing database was rejected to avoid coupling unrelated services.

