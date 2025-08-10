from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
import time
import csv
import logging
import re
from auth_utils import switch_to_admin_role as _switch_to_admin_role

logger = logging.getLogger(__name__)

# ── Phase 1: List Values Navigation & Scrape ─────────────────────────────
ADMIN_ROLE_URL = (
    "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~19522~1073~N"
)


def switch_to_admin_role(driver):
    """Switch the current session to an administrator role, handling 2FA if
    necessary."""

    _switch_to_admin_role(driver, ADMIN_ROLE_URL)


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
            link = row.find_element(By.CSS_SELECTOR, "td:first-child a")
            name = link.text.strip()
            href = link.get_attribute("href")
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
                By.CSS_SELECTOR, "tr.uir-list-row-tr td:first-child"
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
