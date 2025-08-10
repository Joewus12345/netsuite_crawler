import list_values_scraper
from selenium.common.exceptions import NoSuchElementException


class DummyCell:
    def __init__(self, text):
        self.text = text


class DummyRow:
    def __init__(self, elements=None, fallback=None):
        self.elements = elements or {}
        self.fallback = fallback

    def find_elements(self, by, selector):
        if selector in self.elements:
            return [DummyCell(self.elements[selector])]
        return []

    def find_element(self, by, selector):
        if selector == "td:nth-child(2)" and self.fallback is not None:
            return DummyCell(self.fallback)
        raise NoSuchElementException


def test_extract_value_with_tooltip():
    row = DummyRow(elements={'td[data-ns-tooltip="Value"]': 'Logged'})
    assert list_values_scraper._extract_value_from_row(row) == "Logged"


def test_extract_value_with_label():
    row = DummyRow(elements={'td[data-label="Value"]': 'Escalated'})
    assert list_values_scraper._extract_value_from_row(row) == "Escalated"


def test_extract_value_fallback_second_cell():
    row = DummyRow(fallback='Other')
    assert list_values_scraper._extract_value_from_row(row) == "Other"
