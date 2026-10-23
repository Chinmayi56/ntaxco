import pytest

from common_validation import normalize_indian_mobile, require_indian_mobile


@pytest.mark.parametrize("value", [
    "6123456789", "7123456789", "8123456789", "9123456789",
])
def test_accepts_exactly_ten_digit_indian_mobile(value):
    assert normalize_indian_mobile(value) == value


@pytest.mark.parametrize("value", [
    "", "5123456789", "0123456789", "612345678", "61234567890",
    "61234abc89", "61234-6789", "61234 6789", "+916123456789",
    None, 6123456789,
])
def test_rejects_invalid_mobile_by_default(value):
    assert normalize_indian_mobile(value) is None


def test_explicit_plus91_workflow_normalizes_prefix():
    assert normalize_indian_mobile("+916123456789", allow_plus91=True) == "6123456789"


@pytest.mark.parametrize("value", ["+91 6123456789", "+91-6123456789", "abc6123456789"])
def test_explicit_plus91_does_not_strip_punctuation_or_letters(value):
    assert normalize_indian_mobile(value, allow_plus91=True) is None


def test_require_mobile_raises_clear_validation_error():
    with pytest.raises(ValueError, match="10-digit"):
        require_indian_mobile("123")
