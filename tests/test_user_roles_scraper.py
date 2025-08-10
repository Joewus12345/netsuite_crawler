import user_roles_scraper
from selenium.common.exceptions import NoSuchElementException
from unittest.mock import Mock


class DummyCell:
    def __init__(self, text):
        self.text = text


class DummyRow:
    def __init__(self, texts):
        self.texts = texts

    def find_elements(self, by, selector):
        if selector == "td":
            return [DummyCell(t) for t in self.texts]
        return []


class DummyTable:
    def __init__(self, rows):
        self.rows = rows

    def find_elements(self, by, selector):
        if selector == "tr.uir-machine-row":
            return [DummyRow(r) for r in self.rows]
        return []


def test_parse_table_rows_limits_cells_and_skips_incomplete_rows():
    table = DummyTable([
        ["A", "B", "C"],
        ["D", "E"],
        ["F"],
    ])
    result = user_roles_scraper._parse_table_rows(table, 2)
    assert result == [["A", "B"], ["D", "E"]]


def test_scrape_permission_section_missing_tab_returns_empty(monkeypatch):
    driver = Mock()
    driver.find_element.side_effect = NoSuchElementException
    result = user_roles_scraper._scrape_permission_section(
        driver, "missingtab", "table_id", 2
    )
    assert result == []
    driver.find_element.assert_called_once()
