from .base import ProviderResult


class MockProvider:
    id = "mock"
    name = "Mock provider"

    def capabilities(self) -> dict[str, object]:
        return {"photo": True, "video": True, "upload": True, "submit_metadata": True, "status": True, "mode": "mock"}

    def upload(self, *, asset_path: str, asset_sha256: str, metadata: dict[str, object], idempotency_key: str) -> ProviderResult:
        return ProviderResult(state="UPLOADED", remote_key=f"mock:{asset_sha256[:16]}", message="Mock upload completed")

    def submit_metadata(self, *, remote_key: str, metadata: dict[str, object]) -> ProviderResult:
        return ProviderResult(state="APPROVED", remote_key=remote_key, message="Mock metadata submission completed")

    def get_status(self, *, remote_key: str) -> ProviderResult:
        return ProviderResult(state="APPROVED", remote_key=remote_key, message="Mock provider approved")

