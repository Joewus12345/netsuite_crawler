from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
import time
import csv
import logging
import re
from config import HEADLESS_MODE

logger = logging.getLogger(__name__)

# ── Phase 1: List Values Navigation & Scrape ─────────────────────────────
def switch_to_admin_role(driver):
    """Switch the current session to an administrator role, handling 2FA if
    necessary."""

    url = (
        "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~19522~1073~N"
    )
    logger.info("➡️ Switching to admin role…")
    driver.get(url)
    
    # ✅ Handle 2FA Authentication
    if "loginchallenge/entry.nl" in driver.current_url:
        print("🔐 2FA Authentication Required!")

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
                logger.info("✅ 2FA Code Submitted.")

                time.sleep(5)  # Wait for redirection
            except Exception as e:
                logger.error(f"⚠️ Error entering 2FA code: {e}")
                driver.quit()
                return

        else:
            # Non-Headless Mode → User enters 2FA manually
            logger.info("⏳ Waiting for manual 2FA entry in the browser…")
            time.sleep(30)  # Give user 30 seconds to enter the code manually
            
    WebDriverWait(driver, 10).until(EC.url_contains("whence"))
    logger.info("🔄 Switched to admin role.")


def navigate_to_list_values_table(driver):
    """Navigate directly to the NetSuite page that lists all list values."""

    logger.info("➡️ Navigating to List Values table…")
    driver.get("https://4891605.app.netsuite.com/app/common/custom/custlists.nl?whence=")
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "div__footer"))
    )
    logger.info("✅ On List Values Table page.")


def scrape_list_values(driver):
    """Collect list values for each list on the table."""

    logger.info("🔎 Scraping list values…")
    results = {}

    rows = driver.find_elements(By.CSS_SELECTOR, "tr.uir-list-row-tr")
    links = []
    for row in rows:
        try:
            name = row.find_element(By.CSS_SELECTOR, 'td[data-label="Name"]').text.strip()
            last_td = row.find_elements(By.CSS_SELECTOR, "td")[-1]
            href = last_td.find_element(By.TAG_NAME, "a").get_attribute("href")
            links.append((name, href))
        except NoSuchElementException:
            continue

    for name, href in links:
        driver.get(href)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "tr.uir-list-row-tr"))
        )
        values = [
            cell.text.strip()
            for cell in driver.find_elements(
                By.CSS_SELECTOR, 'tr.uir-list-row-tr td[data-label="Name"]'
            )
            if cell.text.strip()
        ]
        results[name] = values

    logger.info("✅ Finished scraping list values.")
    return results


def save_list_values(data, filename="list_values.csv"):
    """Write scraped list values to a CSV file."""

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["list_name", "value"])
        for list_name, values in data.items():
            for value in values:
                writer.writerow([list_name, value])

    logger.info(f"💾 Saved list values to {filename}")
