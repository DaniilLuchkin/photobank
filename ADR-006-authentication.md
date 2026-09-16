# ADR-006: Application authentication independent of network location

Status: Accepted

Use Argon2id password hashes, HttpOnly SameSite sessions, CSRF protection as write endpoints mature, and server-side secret storage. Tailscale/reverse proxy limits exposure but is not the authentication model. Publicly exposing the app without auth is not supported.

