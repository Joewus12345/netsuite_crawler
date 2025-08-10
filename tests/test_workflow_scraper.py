import os
import sys
import types

# Ensure repository root on path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Provide dummy config for workflow_scraper import
sys.modules['config'] = types.SimpleNamespace(SECURITY_ANSWER='answer', HEADLESS_MODE=True)

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException

import workflow_scraper


class DummySpan:
    def __init__(self, text):
        self.text = text


class DummyActionElement:
    def __init__(self, span_text=None, onmouseover=None):
        self._span_text = span_text
        self._onmouseover = onmouseover

    def find_element(self, by, selector):
        if self._span_text is not None:
            return DummySpan(self._span_text)
        raise NoSuchElementException()

    def get_attribute(self, name):
        if name == "onmouseover":
            return self._onmouseover
        return None


def test_extract_action_arguments_from_span():
    el = DummyActionElement(span_text="  span args  ", onmouseover="actionArguments: 'ignored'")
    assert workflow_scraper.extract_action_arguments(el) == "span args"


def test_extract_action_arguments_from_onmouseover():
    el = DummyActionElement(onmouseover="someFunc(); actionArguments: 'hover args'")
    assert workflow_scraper.extract_action_arguments(el) == "hover args"


class DummyTextElement:
    def __init__(self, text):
        self.text = text


class FlakyFindBase:
    """Simulate element whose find_element raises before succeeding."""
    def __init__(self, responses):
        self._responses = iter(responses)

    def find_element(self, by, selector):
        result = next(self._responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_safe_find_text_retries(monkeypatch):
    base = FlakyFindBase([
        NoSuchElementException(),
        StaleElementReferenceException(),
        DummyTextElement("final")
    ])
    assert workflow_scraper.safe_find_text(base, None, None, retries=5, delay=0) == "final"


class FlakyAttrBase:
    """Simulate element whose get_attribute raises before succeeding."""
    def __init__(self, responses):
        self._responses = iter(responses)

    def get_attribute(self, name):
        result = next(self._responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_safe_get_attr_retries(monkeypatch):
    base = FlakyAttrBase([
        StaleElementReferenceException(),
        "value"
    ])
    assert workflow_scraper.safe_get_attr(base, "data", retries=3, delay=0) == "value"
