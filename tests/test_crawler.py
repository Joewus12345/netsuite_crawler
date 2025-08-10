import os
import sys
import types
import requests

# Ensure repository root on path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Provide dummy config module required by crawler
sys.modules['config'] = types.SimpleNamespace(
    NETSUITE_URL='',
    NETSUITE_EMAIL='',
    NETSUITE_PASSWORD='',
    SECURITY_ANSWER='',
    ADMIN_ITEM_URL='',
    HEADLESS_MODE=True,
)

import crawler


class MockDriver:
    def __init__(self, html):
        self.page_source = html
        self.get_called_with = None

    def get(self, url):
        self.get_called_with = url


def test_extract_links_fallback_to_selenium(monkeypatch):
    """requests.get raises, so extract_links should use Selenium's page_source."""
    def mock_get(url, timeout):
        raise requests.RequestException("failure")

    monkeypatch.setattr(crawler.requests, "get", mock_get)
    monkeypatch.setattr(crawler.time, "sleep", lambda _: None)

    html = '<html><body><a href="/page">Link</a></body></html>'
    driver = MockDriver(html)

    links = crawler.extract_links(driver, "https://example.com")

    assert driver.get_called_with == "https://example.com"
    assert links == {"https://example.com/page"}


def test_extract_links_unique_absolute_urls(monkeypatch):
    """When requests.get succeeds, unique absolute URLs are returned."""
    html = (
        '<html><body>'
        '<a href="/page1">Page1</a>'
        '<a href="/page1">Duplicate</a>'
        '<a href="https://example.com/page2">Page2</a>'
        '</body></html>'
    )

    class Response:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    monkeypatch.setattr(crawler.requests, "get", lambda url, timeout: Response(html))

    links = crawler.extract_links(None, "https://example.com")

    assert links == {"https://example.com/page1", "https://example.com/page2"}
