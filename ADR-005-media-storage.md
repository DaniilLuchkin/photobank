# ADR-005: Bind-mounted managed storage with database references

Status: Accepted

Originals live under a persistent host root and are never deleted automatically. Derivatives and provider exports are separate. The database stores asset IDs, canonical paths, size and hashes. Moving/renaming files is an explicit operation so a watcher cannot silently break provenance.

