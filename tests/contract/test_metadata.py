from campaign_agent.contract.field_metadata import METADATA, bounds, defaults, iter_paths
from campaign_agent.contract.model import CampaignOverlay, LeadgenFile


def test_every_key_of_the_full_file_has_metadata() -> None:
    assert set(iter_paths(LeadgenFile)) == set(METADATA)


def test_overlay_only_contains_campaign_owned_keys() -> None:
    for path in iter_paths(CampaignOverlay):
        assert METADATA[path].owner == "campaign", (
            f"{path} is {METADATA[path].owner}, not campaign-owned"
        )


def test_dead_and_infrastructure_keys_are_not_in_the_overlay() -> None:
    overlay = set(iter_paths(CampaignOverlay))
    for path, meta in METADATA.items():
        if meta.owner in ("dead", "infra", "base"):
            assert path not in overlay


def test_every_campaign_owned_key_is_emittable() -> None:
    overlay = set(iter_paths(CampaignOverlay))
    assert {p for p, m in METADATA.items() if m.owner == "campaign"} == overlay


def test_defaults_and_bounds_come_from_the_model() -> None:
    assert defaults()["search.queries_per_run"] == 16
    assert defaults()["qualification.accept_service_companies"] is False
    assert bounds()["search.queries_per_run"] == {"ge": 1, "le": 40}
    assert bounds()["qualification.min_confidence"] == {"ge": 0, "le": 1}


def test_conflicting_ma_defaults_are_documented() -> None:
    assert "contacts.py" in str(METADATA["qualification.accept_service_companies"].ma_code_default)
