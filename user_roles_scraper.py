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

# ── Phase 1: User Roles Navigation & Scrape ─────────────────────────────
ADMIN_ROLE_URL = (
    "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~19522~1073~N"
)


def switch_to_admin_role(driver):
    """Switch the current session to an administrator role, handling 2FA if
    necessary."""

    _switch_to_admin_role(driver, ADMIN_ROLE_URL)


def navigate_to_user_roles_list(driver):
    """Navigate directly to the NetSuite page that lists all user roles."""

    logger.info("➡️ Navigating to User Roles list…")
    driver.get("https://4891605.app.netsuite.com/app/setup/rolelist.nl?whence=")
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "div__footer"))
    )
    logger.info("✅ On User Roles List page.")


def _parse_table_rows(table, num_cols):
    """Return a list of the first ``num_cols`` text values from each table row."""

    rows = []
    for tr in table.find_elements(By.CSS_SELECTOR, "tr.uir-machine-row"):
        cells = [c.text.strip() for c in tr.find_elements(By.TAG_NAME, "td")[:num_cols]]
        if len(cells) == num_cols:
            rows.append(cells)
    return rows


def _scrape_permission_section(driver, tab_id, table_id, num_cols):
    """Click a permission subtab and parse its table rows."""

    try:
        subtab = driver.find_element(By.ID, f"{tab_id}txt")
        driver.execute_script("arguments[0].click();", subtab)
        logger.info(f"      ▶️ {tab_id} tab opened")
    except NoSuchElementException:
        logger.warning(f"      ⚠️ {tab_id} tab not found")
        return []
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, table_id))
    )
    table = driver.find_element(By.ID, table_id)
    rows = _parse_table_rows(table, num_cols)
    logger.info(f"      ✔️ scraped {len(rows)} rows from {tab_id}")
    return rows


def scrape_permissions_for_role(driver, role_name, results):
    """Scrape permission tables for a single role and append to ``results``."""

    logger.info(f"  🔍 Scraping permissions for role: {role_name}")
    sections = [
        ("Transactions", "tranmach", "tranmach_splits", 2),
        ("Reports", "repomach", "repomach_splits", 2),
        ("Lists", "listsmach", "listsmach_splits", 2),
        ("Setup", "setupmach", "setupmach_splits", 2),
        ("Custom Record", "custrecordmach", "custrecordmach_splits", 3),
    ]
    for label, tab_id, table_id, cols in sections:
        logger.info(f"    ➡️ {label} subtab")
        rows = _scrape_permission_section(driver, tab_id, table_id, cols)
        for row in rows:
            if cols == 2:
                perm, level = row
                results.append([role_name, label, perm, level, ""])
            else:
                record, level, restrict = row
                results.append([role_name, label, record, level, restrict])
        logger.info(f"    ✔️ {label}: {len(rows)} entries")


def scrape_all_user_roles(driver):
    """Iterate through all pages of roles and collect permission data."""

    results = []
    page = 1
    while True:
        logger.info(f"🔄 Processing roles page {page}")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "div__footer"))
        )
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.uir-list-row-tr"))
        )
        main_window = driver.current_window_handle
        rows = driver.find_elements(By.CSS_SELECTOR, "tr.uir-list-row-tr")
        for i in range(len(rows)):
            # Re-fetch the row each time to avoid stale element references after navigation
            row = driver.find_elements(By.CSS_SELECTOR, "tr.uir-list-row-tr")[i]
            try:
                link = row.find_element(By.CSS_SELECTOR, "td:nth-child(3) a")
            except NoSuchElementException:
                continue
            role_name = link.text.strip()
            logger.info(f"➡️ Opening role: {role_name}")
            link_url = link.get_attribute("href")
            driver.execute_script("window.open(arguments[0]);", link_url)
            driver.switch_to.window(driver.window_handles[-1])
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "PERM_TABlnk"))
            )
            scrape_permissions_for_role(driver, role_name, results)
            driver.close()
            driver.switch_to.window(main_window)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "div__footer"))
            )
        try:
            pagination = driver.find_element(By.ID, "segment_fs")
            # ensure control is initialized
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('pointerenter', {bubbles:true}));",
                pagination,
            )
            next_btn = pagination.find_element(By.CSS_SELECTOR, "button.navig-next")
            if "disabled" in next_btn.get_attribute("class"):
                logger.info("ℹ️ Reached final page of roles")
                break

            input_el = driver.find_element(By.ID, "segment")
            page_label = pagination.get_attribute("data-pagination-text") or input_el.get_attribute("value")
            old_value = input_el.get_attribute("value") or ""
            parts = old_value.split(chr(2))
            try:
                current_index = int(parts[0]) if parts and parts[0].isdigit() else 0
            except Exception:
                logger.error(f"Unexpected pagination value: {old_value!r}")
                break

            logger.info(f"➡️ Moving to next page… (currently {page_label})")
            first_row = driver.find_element(By.CSS_SELECTOR, "tr.uir-list-row-tr td:nth-child(3)").text

            next_index = current_index + 1
            driver.execute_script(
                "NS.UI.Helpers.PaginationSelect.goToPage(arguments[0], arguments[1]);",
                pagination,
                next_index,
            )

            WebDriverWait(driver, 10).until(
                lambda d: input_el.get_attribute("value") != old_value
            )
            WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.CSS_SELECTOR, "tr.uir-list-row-tr td:nth-child(3)").text != first_row
            )
            page += 1
            new_label = pagination.get_attribute("data-pagination-text")
            logger.info(f"✅ Now on roles page {page} ({new_label})")
        except Exception as e:
            logger.error(f"⚠️ Failed to navigate to next page: {e}")
            break
    return results


def save_permissions(results, filename="user_role_permissions.csv"):
    """Write permission rows to ``filename`` in CSV format."""

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Role", "Section", "Permission/Record", "Level", "Restrict"])
        writer.writerows(results)
    logger.info(f"📂 Saved user role permissions to {filename}")


def run(driver):
    """Run the complete user-role scraping workflow."""

    switch_to_admin_role(driver)
    navigate_to_user_roles_list(driver)
    results = scrape_all_user_roles(driver)
    save_permissions(results)
