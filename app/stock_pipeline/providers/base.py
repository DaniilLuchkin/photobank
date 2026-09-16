from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    state: str
    remote_key: str | None = None
    message: str = ""
    manual_required: bool = False


class StockProvider(Protocol):
    id: str
    name: str

    def capabilities(self) -> dict[str, Any]: ...

    def upload(self, *, asset_path: str, asset_sha256: str, metadata: dict[str, Any], idempotency_key: str) -> ProviderResult: ...

    def submit_metadata(self, *, remote_key: str, metadata: dict[str, Any]) -> ProviderResult: ...

    def get_status(self, *, remote_key: str) -> ProviderResult: ...

