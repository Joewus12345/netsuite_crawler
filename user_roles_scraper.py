from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
import time
import csv
from config import HEADLESS_MODE

# ── Phase 1: User Roles Navigation & Scrape ─────────────────────────────
def switch_to_admin_role(driver):
    """Switch the current session to an administrator role, handling 2FA if
    necessary."""

    url = (
        "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~19522~1073~N"
    )
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
                print("✅ 2FA Code Submitted.")

                time.sleep(5)  # Wait for redirection
            except Exception as e:
                    print(f"⚠️ Error entering 2FA code: {e}")
                    driver.quit()
                    return

        else:
            # Non-Headless Mode → User enters 2FA manually
            print("⏳ Waiting for manual 2FA entry in the browser...")
            time.sleep(30)  # Give user 30 seconds to enter the code manually
            
    WebDriverWait(driver, 10).until(EC.url_contains("whence"))
    print("🔄 Switched to admin role.")


def navigate_to_user_roles_list(driver):
    """Navigate directly to the NetSuite page that lists all user roles."""

    driver.get("https://4891605.app.netsuite.com/app/setup/rolelist.nl?whence=")
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "div__footer"))
    )
    print("✅ On User Roles List page.")


def _parse_table_rows(table, num_cols):
    """Return a list of the first ``num_cols`` text values from each table row."""

    rows = []
    for tr in table.find_elements(By.CSS_SELECTOR, "tr.uir-machine-row"):
        cells = [c.text.strip() for c in tr.find_elements(By.TAG_NAME, "td")[:num_cols]]
        if len(cells) == num_cols:
            rows.append(cells)
    return rows


def _scrape_permission_section(driver, tab_js, table_id, num_cols):
    """Click a permission subtab and parse its table rows."""

    try:
        driver.find_element(
            By.CSS_SELECTOR, f"a[href=\"javascript:void('{tab_js}')\"]"
        ).click()
    except NoSuchElementException:
        return []
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, table_id))
    )
    table = driver.find_element(By.ID, table_id)
    return _parse_table_rows(table, num_cols)


def scrape_permissions_for_role(driver, role_name, results):
    """Scrape permission tables for a single role and append to ``results``."""

    sections = [
        ("Transactions", "tranmach", "tranmach_splits", 2),
        ("Setup", "setupmach", "setupmach_splits", 2),
        ("Custom Record", "custrecordmach", "custrecordmach_splits", 3),
    ]
    for label, jsref, table_id, cols in sections:
        rows = _scrape_permission_section(driver, jsref, table_id, cols)
        for row in rows:
            if cols == 2:
                perm, level = row
                results.append([role_name, label, perm, level, ""])
            else:
                record, level, restrict = row
                results.append([role_name, label, record, level, restrict])


def scrape_all_user_roles(driver):
    """Iterate through all pages of roles and collect permission data."""

    results = []
    while True:
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.uir-list-row-tr"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "tr.uir-list-row-tr")
        for row in rows:
            try:
                link = row.find_element(By.CSS_SELECTOR, "td:nth-child(3) a")
            except NoSuchElementException:
                continue
            role_name = link.text.strip()
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
            next_btn = driver.find_element(By.CSS_SELECTOR, "button.navig-next")
            if "disabled" in next_btn.get_attribute("class"):
                break
            next_btn.click()
            WebDriverWait(driver, 10).until(EC.staleness_of(rows[0]))
        except Exception:
            break
    return results


def save_permissions(results, filename="user_role_permissions.csv"):
    """Write permission rows to ``filename`` in CSV format."""

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Role", "Section", "Permission/Record", "Level", "Restrict"])
        writer.writerows(results)
    print(f"📂 Saved user role permissions to {filename}")
