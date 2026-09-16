from __future__ import annotations

from ftplib import FTP_TLS
from pathlib import Path
from typing import Any

from .base import ProviderResult


class ShutterstockFTPSProvider:
    """Official FTPS transfer adapter; portal submission remains manual."""

    id = "shutterstock"
    name = "Shutterstock FTPS"

    def __init__(self, host: str, username: str, password: str, remote_dir: str = "") -> None:
        self.host = host
        self.username = username
        self.password = password
        self.remote_dir = remote_dir

    def capabilities(self) -> dict[str, Any]:
        return {"photo": True, "video": True, "ftps": True, "submit_metadata": False, "status": False}

    def upload(self, *, asset_path: str, asset_sha256: str, metadata: dict[str, Any], idempotency_key: str) -> ProviderResult:
        path = Path(asset_path)
        remote_name = f"{idempotency_key[:24]}-{path.name}"
        try:
            with FTP_TLS(self.host, timeout=60) as ftp:
                ftp.login(self.username, self.password)
                ftp.prot_p()
                if self.remote_dir:
                    ftp.cwd(self.remote_dir)
                with path.open("rb") as stream:
                    ftp.storbinary(f"STOR {remote_name}", stream)
        except (OSError, EOFError) as exc:
            return ProviderResult(state="MANUAL_REQUIRED", message=f"Ambiguous FTPS transfer; verify {remote_name}: {exc}", manual_required=True)
        return ProviderResult(state="UPLOADED", remote_key=remote_name, message="FTPS transfer completed; portal submission required")

    def submit_metadata(self, *, remote_key: str, metadata: dict[str, Any]) -> ProviderResult:
        return ProviderResult(state="MANUAL_REQUIRED", remote_key=remote_key, message="Apply CSV/release metadata and submit in Shutterstock portal", manual_required=True)

    def get_status(self, *, remote_key: str) -> ProviderResult:
        return ProviderResult(state="MANUAL_REQUIRED", remote_key=remote_key, message="Status must be checked in Shutterstock portal", manual_required=True)
