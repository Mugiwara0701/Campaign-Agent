import pytest

from campaign_agent.guard.input_guard import MAX_CHARS, MAX_EDIT_CHARS, check_input
from tests.unit import fake_secrets as fs


def codes(text: str, kind: str = "message") -> list[str]:
    return [i.code for i in check_input(text, kind).issues]  # type: ignore[arg-type]


def test_normal_text_passes_and_is_cleaned() -> None:
    result = check_input("  Find US companies\r\n\r\n\r\n\r\nbuilding kiosks \n")
    assert result.ok
    assert result.text == "Find US companies\n\nbuilding kiosks"


def test_unicode_is_normalised() -> None:
    assert check_input("café kiosks").text == "café kiosks"


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t "])
def test_empty_text_is_rejected(text: str) -> None:
    assert codes(text) == ["empty"]


def test_size_limits_follow_the_dashboard_and_edits_are_shorter() -> None:
    assert check_input("a " * (MAX_CHARS // 2)).ok
    assert codes("a" * (MAX_CHARS + 1)) == ["too_long"]
    assert codes("a" * (MAX_EDIT_CHARS + 1), "edit") == ["too_long"]
    assert codes("a" * (MAX_CHARS * 10)) == ["too_long"]


@pytest.mark.parametrize(
    "text", ["bell\x07", "null\x00byte", "esc\x1b[31m", "rtl ‮ override", "zero​width"]
)
def test_control_and_invisible_characters_are_rejected(text: str) -> None:
    assert codes(text) == ["control_characters"]


@pytest.mark.parametrize(
    "text",
    [
        "here is my key " + fs.AWS,
        "token " + fs.GITHUB,
        "slack " + fs.SLACK,
        "use " + fs.SK_KEY,
        fs.GOOGLE,
        "jwt " + fs.JWT,
        "connect to " + fs.URL_PASSWORD,
        "password: correcthorsebattery",
        fs.API_KEY_LINE,
        fs.PRIVATE_KEY,
    ],
)
def test_secret_like_strings_are_rejected_without_being_echoed(text: str) -> None:
    result = check_input(text)
    assert not result.ok
    assert result.text == ""
    assert {i.code for i in result.issues} == {"secret_like"}
    for issue in result.issues:
        for chunk in ("AKIAABC", "ghp_abc", "hunter2", "correcthorse", "abcdef123456", "MIIE"):
            assert chunk not in issue.message


def test_words_that_only_look_like_secrets_are_fine() -> None:
    assert check_input(
        "Companies building password manager devices and token-based access control"
    ).ok
    assert check_input("find kiosk makers; the api key management product category").ok


def test_non_english_text_is_rejected() -> None:
    assert codes("请找到美国的汽车公司并发送电子邮件给他们") == ["unsupported_language"]
    assert check_input("Find companies in Zürich and São Paulo building kiosks").ok
    assert check_input("Target 500-5,000 employee firms (R&D, 2026!)").ok
