# ADR-004: Stock provider adapters

Status: Accepted

Core pipeline depends on `StockProvider` capabilities and independent provider jobs. Transfer, metadata transformation, submission and status are separate operations. Official FTP/FTPS/SFTP is allowed where documented; unsupported portal actions become `MANUAL_REQUIRED`. Browser automation was rejected because it is fragile, hard to make idempotent and may violate provider rules.

