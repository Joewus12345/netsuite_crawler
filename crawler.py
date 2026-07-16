# from selenium import webdriver  # Controls the browser
from selenium.webdriver.common.by import By  # Find elements
# from selenium.webdriver.common.keys import Keys  # Send input
from selenium.webdriver.support.ui import WebDriverWait  # Handle delays
from selenium.webdriver.support import expected_conditions as EC  # Handle dynamic elements
from config import NETSUITE_BASE_URL
import time  # For delays
import logging
from urllib.parse import urljoin  # Handle URLs
import requests  # requests for HTTP requests
from bs4 import BeautifulSoup  # Parses HTML to extract links
from auth_utils import tick_remember_device_if_present
from config import (
    NETSUITE_URL,
    NETSUITE_EMAIL,
    NETSUITE_PASSWORD,
    SECURITY_ANSWER,
    ADMIN_ITEM_URL,
    HEADLESS_MODE,
)  # Import credentials

# ✅ Configure WebDriver (Allow headless mode)

# Set up WebDriver options
# options = webdriver.ChromeOptions()
# if HEADLESS_MODE:
    # options.add_argument("--headless")  # Enable headless mode
    # options.add_argument("--disable-gpu")  # Necessary for headless on Windows
    # options.add_argument("--window-size=1920,1080")  # Set browser size
# driver = webdriver.Chrome(options=options)

logger = logging.getLogger(__name__)

def is_netsuite_logged_in(driver, timeout=8):
    """
    Checks whether the persistent browser profile already has a valid NetSuite session.
    """
    driver.get(f"{NETSUITE_BASE_URL}/app/center/card.nl?whence=")
    time.sleep(2)

    current_url = driver.current_url.lower()

    login_indicators = [
        "/app/login/",
        "login.nl",
        "securityquestions.nl",
        "loginchallenge/entry.nl",
    ]

    if any(marker in current_url for marker in login_indicators):
        return False

    try:
        WebDriverWait(driver, timeout).until(
            lambda d: "app.netsuite.com" in d.current_url.lower()
        )
        logger.info("✅ Existing NetSuite browser session/profile is valid.")
        return True
    except Exception:
        return False


def login_netsuite(driver):
    """Check for existing NetSuite Session before the NetSuite login workflow logic"""
    if is_netsuite_logged_in(driver):
        logger.info("✅ Skipping login because existing NetSuite session is active.")
        return True
    """Logs into NetSuite, handles 2FA dynamically, and navigates to the 'Admin Item' Custom Record."""
    driver.get(NETSUITE_URL)

    try:
        # Wait for email input
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "email")))

        # Enter email & password
        driver.find_element(By.ID, "email").send_keys(NETSUITE_EMAIL)
        driver.find_element(By.ID, "password").send_keys(NETSUITE_PASSWORD)

        # Click login
        driver.find_element(By.ID, "login-submit").click()
        time.sleep(7)

        # ✅ Print the current URL for debugging
        print(f"🔍 Current page URL: {driver.current_url}")

        # ✅ Handle 2FA Authentication
        if "loginchallenge/entry.nl" in driver.current_url:
            print("🔐 2FA Authentication Required!")
            tick_remember_device_if_present(driver)

            if HEADLESS_MODE:
                # Headless Mode → Enter 2FA Code in Console
                two_fa_code = input("🔢 Enter 2FA Code: ")  # Prompt user for 6-digit code

                try:
                    # Wait for the 2FA input field
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.ID, "uif56_input"))
                    )

                    # Enter the 2FA code from the console
                    two_fa_input = driver.find_element(By.ID, "uif56_input")
                    two_fa_input.send_keys(two_fa_code)
                    print("✅ 2FA Code Entered.")

                    # Wait for the submit button
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-type='primary'][role='button']"))
                    )

                    # Click submit using JavaScript (since it's inside a <div>)
                    submit_button = driver.find_element(By.CSS_SELECTOR, "div[data-type='primary'][role='button']")
                    driver.execute_script("arguments[0].click();", submit_button)
                    print("✅ 2FA Code Submitted.")

                    time.sleep(5)  # Wait for redirection
                except Exception as e:
                    print(f"⚠️ Error entering 2FA code: {e}")
                    driver.quit()
                    return

            else:
                # Non-Headless Mode → User enters 2FA manually
                print("⏳ Waiting for manual 2FA entry in the browser...")
                WebDriverWait(driver, 180).until(
                    lambda d: "loginchallenge/entry.nl" not in d.current_url
                )
                print("✅ Manual 2FA completed.")
                time.sleep(3)

        # ✅ Handle security questions
        if "securityquestions.nl" in driver.current_url:
            print("🔐 Security questions detected! Answering...")

            try:
                # Wait for answer input field
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='answer'][type='password']"))
                )
                driver.find_element(By.CSS_SELECTOR, "input[name='answer'][type='password']").send_keys(SECURITY_ANSWER)
                print("✅ Answered security question.")

                # Click submit
                driver.find_element(By.CSS_SELECTOR, "input[name='submitter'][type='submit']").click()
                time.sleep(5)

            except Exception as e:
                print(f"⚠️ Error filling the security answer: {e}")

        # ✅ Navigate directly to Admin Item page
        # time.sleep(5)
        print("✅ Login successful! Handing over control to main.py...")
        # navigate_to_admin_item(driver)

    except Exception as e:
        print(f"⚠️ Error during login: {e}")
        driver.quit()

def navigate_to_admin_item(driver):
    """Navigates directly to the 'Admin Item' Custom Record page and waits."""
    driver.get(ADMIN_ITEM_URL)
    print("🔍 Navigated to Custom Record: 'Admin Item'.")

    try:
        # Wait until the page loads (adjust selector as needed)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))

        # ✅ Keep the page open until the user manually closes
        print("🔵 Press 'Enter' to close the crawler manually...")
        input()  # Wait for user input
        driver.quit()  # Close browser when user presses Enter

    except Exception as e:
        print(f"⚠️ Error navigating to 'Admin Item': {e}")

def extract_links(driver, url):
    """Extracts all links from a given webpage using BeautifulSoup & requests (faster than Selenium)."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
    except:
        # Fall back to Selenium if requests fail (e.g., need authentication)
        driver.get(url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, "html.parser")

    links = {urljoin(url, a["href"]) for a in soup.find_all("a", href=True)}
    return links

def crawl_netsuite(driver):
    """Crawls NetSuite after login and extracts all links."""
    visited = set()
    to_visit = {driver.current_url}  # Start from the dashboard

    while to_visit:
        current_url = to_visit.pop()
        print(f"🔍 Crawling: {current_url}")

        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            new_links = extract_links(driver, current_url)
            to_visit.update(new_links - visited)
        except Exception as e:
            print(f"⚠️ Error crawling {current_url}: {e}")

    print("\n✅ Crawling complete!")
    print(f"Total Links Found: {len(visited)}")
    print("\n".join(visited))
    return visited


def run(driver):
    """Run the standalone site crawler.

    Assumes ``driver`` is already logged into NetSuite.  Returns the set of
    visited links for further processing if needed.
    """

    return crawl_netsuite(driver)
