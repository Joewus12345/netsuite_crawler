from selenium import webdriver  # Controls the browser
from selenium.webdriver.common.by import By  # Find elements
from selenium.webdriver.common.keys import Keys  # Send input
from selenium.webdriver.support.ui import WebDriverWait  # Handle delays
from selenium.webdriver.support import expected_conditions as EC  # Handle dynamic elements
import time  # For delays
from urllib.parse import urljoin  # Handle URLs
import requests  # requests for HTTP requests
from bs4 import BeautifulSoup  # Parses HTML to extract links
from config import NETSUITE_URL, NETSUITE_EMAIL, NETSUITE_PASSWORD, SECURITY_ANSWER  # Import security question answers

# Set up WebDriver options
options = webdriver.ChromeOptions()
# options.add_argument("--headless")  # Run in the background if needed
driver = webdriver.Chrome(options=options)

def login_netsuite(driver):
    """Logs into NetSuite using Selenium and starts crawling after successful login."""
    driver.get(NETSUITE_URL)

    try:
        # Wait for email input to load
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "email")))

        # Enter email
        email_field = driver.find_element(By.ID, "email")
        email_field.send_keys(NETSUITE_EMAIL)

        # Enter password
        password_field = driver.find_element(By.ID, "password")
        password_field.send_keys(NETSUITE_PASSWORD)

        # Click login button
        login_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "login-submit")))
        login_button.click()

        # Wait for redirection
        time.sleep(7)

        # ✅ Print the current URL for debugging
        print(f"🔍 Current page URL: {driver.current_url}")

        # ✅ Check if redirected to the security questions page
        if "securityquestions.nl" in driver.current_url:
            print("🔐 Security questions detected! Answering...")

            try:
                # Wait for the answer input field to appear
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='answer'][type='password']"))
                )

                # Locate the answer input field directly
                answer_input = driver.find_element(By.CSS_SELECTOR, "input[name='answer'][type='password']")

                # Fill in the answer
                answer_input.send_keys(SECURITY_ANSWER)
                print(f"✅ Answered security question.")

                # Click submit
                submit_button = driver.find_element(By.CSS_SELECTOR, "input[name='submitter'][type='submit']")
                submit_button.click()

                # ✅ Wait for page change instead of re-answering
                old_url = driver.current_url
                for _ in range(15):  # Check for 15 seconds
                    time.sleep(1)
                    if driver.current_url != old_url:
                        break  # Page has changed
                else:
                    print("⚠️ Still on the security question page. NetSuite may be rejecting the login.")

            except Exception as e:
                print(f"⚠️ Error finding or filling the answer input field: {e}")

        # ✅ Check if redirected to the dashboard or role selection page
        time.sleep(5)  # Additional delay for redirection
        current_url = driver.current_url
        print(f"🔍 Checking post-login page: {current_url}")

        # ✅ Detect dashboard URL pattern
        if "dashboard" in current_url or "home.nl" in current_url or "app/center/card.nl" in current_url:
            print("✅ Login successful! Bot has reached the dashboard.")
            crawl_netsuite(driver)  # Start crawling immediately

        elif "role" in current_url.lower():
            print("🔄 Role selection detected! Choosing role...")

            try:
                # Locate the first available role
                role_element = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.LINK_TEXT, "Choose Role"))
                )
                role_element.click()
                time.sleep(5)  # Wait for redirection
                print("✅ Role selected! Proceeding to dashboard.")
                crawl_netsuite(driver)  # Start crawling after role selection

            except Exception as e:
                print(f"⚠️ Failed to select role: {e}")

        else:
            print("❌ Login failed! CAPTCHA or unknown page detected.")
            print(f"🚨 Please check the browser manually: {current_url}")
            driver.quit()
            exit()

    except Exception as e:
        print(f"⚠️ Error during login: {e}")
        driver.quit()
        exit()

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

    driver.quit()  # ✅ Close browser after crawling is finished
