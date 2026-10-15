import hashlib

import yaml

from campaign_agent.compiler.complete import (
    base_fingerprint,
    base_raw,
    complete_file,
    fingerprint,
    merge_raw,
)
from campaign_agent.contract.loader import load_full
from campaign_agent.ma_link.service import LinkService, LiveCampaign

OVERLAY = """\
config_version: '1.0'
project_context:
  name: Test
  industries: [automotive]
  products: [EV charging station]
search:
  query_suffix: Germany Austria
exclude_terms: [price]
"""


def test_the_complete_file_is_the_base_with_only_the_campaign_changed() -> None:
    built = complete_file(OVERLAY, "cid", 3)
    full = yaml.safe_load(built.text)
    assert full.pop("config_version") == "1.0"
    base = base_raw()
    assert full["project_context"]["products"] == ["EV charging station"]
    assert full["search"]["query_suffix"] == "Germany Austria"
    changed = {
        key
        for section in ("search", "qualification", "limits", "contacts")
        for key in base[section]
        if full[section].get(key) != base[section][key]
    }
    assert changed == {"query_suffix"}  # every other value is exactly the base's
    assert (
        full["exclude_companies"] == base["exclude_companies"]
    )  # the curated lists are kept whole
    assert "price" in full["exclude_terms"] and set(base["exclude_terms"]) <= set(
        full["exclude_terms"]
    )
    load_full(built.text)  # a complete file in the contract's own terms


def test_the_hash_and_the_base_fingerprint_are_stable_and_checkable() -> None:
    built = complete_file(OVERLAY, "cid", 3)
    assert built.sha256 == hashlib.sha256(built.text.encode()).hexdigest()
    assert built.base_sha256 == base_fingerprint() == fingerprint(base_raw())
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint(
        {"b": [1, 2], "a": 1}
    )  # order of keys does not matter
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_merge_never_removes_a_base_exclusion_and_does_not_touch_the_base() -> None:
    base = base_raw()
    before = fingerprint(base)
    merged = merge_raw({"exclude_companies": ["NewCo"], "exclude_terms": []}, base)
    assert (
        "NewCo" in merged["exclude_companies"]
        and len(merged["exclude_companies"]) == len(base["exclude_companies"]) + 1
    )
    assert fingerprint(base) == before


class _Db:
    def __init__(self, overlay: str) -> None:
        self.overlay = overlay

    def transaction(self):  # type: ignore[no-untyped-def]
        from contextlib import contextmanager

        @contextmanager
        def cm():  # type: ignore[no-untyped-def]
            yield object()

        return cm()


def _live(monkeypatch, overlay: str) -> LiveCampaign:  # type: ignore[no-untyped-def]
    row = {
        "campaign_id": "c1",
        "version_no": 2,
        "sha256": hashlib.sha256(overlay.encode()).hexdigest(),
        "contract_version": "1.0",
        "overlay_yaml": overlay,
        "approved_by": "ceo",
        "approved_at": None,
    }
    monkeypatch.setattr(
        "campaign_agent.ma_link.service.repo.live_version_for_ma", lambda conn, respect=False: row
    )
    live = LinkService(_Db(overlay)).live_campaign()  # type: ignore[arg-type]
    assert live is not None
    return live


def test_the_link_sends_the_complete_file_with_its_hash_and_a_tag_that_follows_the_base(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    live = _live(monkeypatch, OVERLAY)
    payload = live.payload()
    assert payload["leadgen_sha256"] == hashlib.sha256(payload["leadgen_yaml"].encode()).hexdigest()
    assert (
        payload["base_sha256"] == base_fingerprint() and payload["overlay_yaml"] == OVERLAY
    )  # the overlay is still sent
    assert live.tag.endswith(
        "." + base_fingerprint()[:8]
    )  # a re-synced base changes the tag, so the agent fetches again


def test_an_overlay_that_cannot_make_a_valid_file_is_sent_alone(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    live = _live(
        monkeypatch, "config_version: '1.0'\nsearch:\n  queries_per_run: 9999\n"
    )  # out of the contract's range
    assert live.leadgen_yaml is None and "leadgen_yaml" not in live.payload()
    assert live.tag.count(".") == 2  # the old tag format
