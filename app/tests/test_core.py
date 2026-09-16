from pathlib import Path

from PIL import Image

from stock_pipeline.providers.mock import MockProvider
from stock_pipeline.providers import ShutterstockFTPSProvider
from stock_pipeline.services.hashing import hamming_distance, image_perceptual_hash, sha256_file
from stock_pipeline.services.state import can_transition


def test_sha256_and_perceptual_hash(tmp_path: Path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    Image.new("RGB", (100, 100), "blue").save(first)
    Image.new("RGB", (100, 100), "blue").save(second)
    assert sha256_file(first) == sha256_file(second)
    assert hamming_distance(image_perceptual_hash(first), image_perceptual_hash(second)) == 0


def test_state_machine_is_conservative():
    assert can_transition("DISCOVERED", "HASHING")
    assert can_transition("QUALITY_CHECKED", "NEEDS_REVIEW")
    assert not can_transition("APPROVED", "QUEUED")


def test_mock_provider_is_idempotent_by_remote_key():
    provider = MockProvider()
    result = provider.upload(asset_path="x.jpg", asset_sha256="abc", metadata={}, idempotency_key="same")
    again = provider.upload(asset_path="x.jpg", asset_sha256="abc", metadata={}, idempotency_key="same")
    assert result.remote_key == again.remote_key == "mock:abc"
    assert provider.submit_metadata(remote_key=result.remote_key, metadata={}).state == "APPROVED"


def test_shutterstock_ftps_adapter_uses_idempotent_remote_name(monkeypatch, tmp_path):
    uploaded = {}

    class FakeFTP:
        def __init__(self, host, timeout):
            uploaded["host"] = host
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def login(self, username, password):
            uploaded["credentials"] = (username, password)
        def prot_p(self):
            uploaded["protected"] = True
        def cwd(self, directory):
            uploaded["directory"] = directory
        def storbinary(self, command, stream):
            uploaded["command"] = command
            uploaded["bytes"] = stream.read()

    monkeypatch.setattr("stock_pipeline.providers.shutterstock.FTP_TLS", FakeFTP)
    path = tmp_path / "sample.jpg"
    path.write_bytes(b"fixture")
    result = ShutterstockFTPSProvider("ftps.example", "user", "pass", "incoming").upload(
        asset_path=str(path), asset_sha256="a" * 64, metadata={}, idempotency_key="b" * 64
    )
    assert result.state == "UPLOADED"
    assert result.remote_key.startswith("b" * 24)
    assert uploaded["protected"] is True
    assert uploaded["bytes"] == b"fixture"
