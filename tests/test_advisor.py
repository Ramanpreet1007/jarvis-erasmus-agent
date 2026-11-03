"""Tests the destination ranking with its own small list, not the demo data."""
import pytest

from erasmus_inbox.advisor import advice_body, recommend

PARTNERS = [
    {"university": "Near", "city": "A", "country": "Italy", "fields": ["Engineering"], "min_grade": 7.0, "language": "English", "min_level": "B2", "places": 4},
    {"university": "Close", "city": "B", "country": "Italy", "fields": ["Engineering"], "min_grade": 8.0, "language": "English", "min_level": "B2", "places": 2},
    {"university": "Stretch", "city": "C", "country": "Spain", "fields": ["Engineering"], "min_grade": 8.4, "language": "English", "min_level": "B2", "places": 2},
    {"university": "TooHigh", "city": "D", "country": "Italy", "fields": ["Engineering"], "min_grade": 9.5, "language": "English", "min_level": "B2", "places": 2},
    {"university": "NeedsC1", "city": "E", "country": "Italy", "fields": ["Engineering"], "min_grade": 6.0, "language": "English", "min_level": "C1", "places": 2},
    {"university": "OtherField", "city": "F", "country": "Italy", "fields": ["Business"], "min_grade": 6.0, "language": "English", "min_level": "B1", "places": 2},
]


def test_fit_labels_and_preferred_country_first():
    result = recommend(8.0, "Engineering", "B2", ["Italy"], PARTNERS)
    assert [(o["university"], o["fit"]) for o in result["options"]] == [("Near", "Strong match"), ("Close", "Good match"), ("Stretch", "Reach")]


def test_reasons_are_given_for_what_is_ruled_out():
    result = recommend(8.0, "Engineering", "B2", ["Italy"], PARTNERS)
    assert any("TooHigh" in r and "9.5" in r for r in result["ruled_out"])
    assert any("NeedsC1" in r and "C1" in r for r in result["ruled_out"])
    assert not any("OtherField" in r for r in result["ruled_out"])


def test_says_so_when_preferred_country_has_no_option():
    result = recommend(8.0, "Engineering", "B2", ["Spain", "Norway"], PARTNERS[:2])
    assert result["no_preferred_match"] and result["options"]


def test_advice_never_promises_a_place_and_handles_no_options():
    result = recommend(8.0, "Engineering", "B2", ["Italy"], PARTNERS)
    assert "isn't guaranteed" in advice_body("Nora", result)
    assert "meeting" in advice_body("Nora", recommend(1.0, "Engineering", "A1", [], PARTNERS))


def test_grade_outside_the_scale_is_rejected():
    with pytest.raises(ValueError):
        recommend(85, "Engineering", "B2", [], PARTNERS)
