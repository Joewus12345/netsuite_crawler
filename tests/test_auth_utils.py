import os
import sys
from unittest.mock import Mock
import types

# Ensure the repository root is on the path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Provide a dummy config module for auth_utils
sys.modules['config'] = types.SimpleNamespace(HEADLESS_MODE=True)
import auth_utils


class DummyWait:
    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, method):
        return True


def test_switch_to_admin_role_headless(monkeypatch):
    driver = Mock()

    def get(url):
        driver.current_url = "https://example.com/loginchallenge/entry.nl"

    driver.get.side_effect = get
    two_input = Mock()
    submit_button = Mock()

    def find_element(by, value):
        if value == "uif56_input":
            return two_input
        if value == "div[data-type='primary'][role='button']":
            return submit_button
        return Mock()

    driver.find_element.side_effect = find_element
    driver.execute_script = Mock()
    driver.quit = Mock()

    monkeypatch.setattr(auth_utils, "HEADLESS_MODE", True)
    monkeypatch.setattr(auth_utils, "WebDriverWait", DummyWait)
    monkeypatch.setattr("builtins.input", lambda _: "123456")
    sleep_mock = Mock()
    monkeypatch.setattr(auth_utils.time, "sleep", sleep_mock)

    auth_utils.switch_to_admin_role(driver, "role_url")

    driver.get.assert_called_once_with("role_url")
    two_input.send_keys.assert_called_once_with("123456")
    driver.execute_script.assert_called_once_with("arguments[0].click();", submit_button)
    sleep_mock.assert_called_once_with(5)
    driver.quit.assert_not_called()


def test_switch_to_admin_role_non_headless(monkeypatch):
    driver = Mock()

    def get(url):
        driver.current_url = "https://example.com/loginchallenge/entry.nl"

    driver.get.side_effect = get
    driver.find_element = Mock()

    monkeypatch.setattr(auth_utils, "HEADLESS_MODE", False)
    monkeypatch.setattr(auth_utils, "WebDriverWait", DummyWait)
    sleep_mock = Mock()
    monkeypatch.setattr(auth_utils.time, "sleep", sleep_mock)

    auth_utils.switch_to_admin_role(driver, "role_url")

    driver.get.assert_called_once_with("role_url")
    sleep_mock.assert_called_once_with(30)
    driver.find_element.assert_not_called()
