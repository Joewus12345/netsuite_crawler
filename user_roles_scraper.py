from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
import time
import csv
import logging
from config import HEADLESS_MODE

logger = logging.getLogger(__name__)

# ── Phase 1: User Roles Navigation & Scrape ─────────────────────────────
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
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.uir-list-row-tr"))
        )
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
            driver.execute_script("arguments[0].click();", link)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "PERM_TABlnk"))
            )
            scrape_permissions_for_role(driver, role_name, results)
            driver.back()
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
            page_label = pagination.find_element(By.CSS_SELECTOR, "span.uir-pagination-label").text
            logger.info(f"➡️ Moving to next page… (currently {page_label})")
            old_label = page_label
            try:
                next_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", next_btn)
            WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.CSS_SELECTOR, "span.uir-pagination-label").text != old_label
            )
            page += 1
            new_label = driver.find_element(By.CSS_SELECTOR, "span.uir-pagination-label").text
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
