from stock_pipeline.services.ai import MockAIProvider


def test_mock_metadata_is_deduplicated_and_cautious():
    payload = MockAIProvider().generate(
        asset_name="British_Coast.jpg",
        media_type="photo",
        analysis={"width": 4000, "height": 3000},
        frames=[],
    )
    assert payload["title"] == "British Coast"
    assert len(payload["keywords"]) == len(set(payload["keywords"]))
    assert payload["location"]["value"] is None

