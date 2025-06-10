# workflow_scraper.py

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import csv

# ── Phase 1: HRA Record Types Extraction ────────────────────────────────────
def switch_to_hra_role(driver):
    """Switch role to PPA Employee Center Social (no OTP)."""
    url = "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~18903~1059~N"
    driver.get(url)
    WebDriverWait(driver, 10).until(EC.url_contains("center/card.nl"))
    print("🔄 Switched to HRA role.")

def extract_hra_record_types(driver):
    """Expand and scrape record names under HRA tab."""
    driver.get("https://4891605.app.netsuite.com/app/center/card.nl?sc=13&whence=")
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CLASS_NAME, "ns-link-button")))
    driver.find_element(By.CLASS_NAME, "ns-link-button").click()  # Expand All
    time.sleep(2)
    elems = driver.find_elements(By.CSS_SELECTOR, "a.ns-searchable-value[target='_self']")
    names = [e.text.strip() for e in elems]
    print(f"✅ HRA record types: {names}")
    return names

# ── Phase 2: Workflow List Navigation & Filter ─────────────────────────────
def switch_to_admin_role(driver):
    """Switch role to PPA Integration Role A (OTP required)."""
    url = "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~18903~1114~N"
    driver.get(url)
    input("🔐 Enter 6-digit OTP in the browser, then press ENTER here...")
    WebDriverWait(driver, 10).until(EC.url_contains("whence"))
    print("🔄 Switched to admin role.")

def navigate_to_workflow_list(driver):
    """Open Workflow List page."""
    driver.get("https://4891605.app.netsuite.com/app/common/workflow/setup/workflowlist.nl?whence=")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "uir-list-row-tr")))
    print("✅ On Workflow List page.")

def filter_by_record_type(driver, record_name):
    """Expand filters, enter record_name in Record Type dropdown, apply."""
    driver.find_element(By.CSS_SELECTOR, ".ns-icon.ns-filters-onoff-button").click()
    WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name^='inpt_Workflow_RECORDTYPE']")))
    fld = driver.find_element(By.CSS_SELECTOR, "input[name^='inpt_Workflow_RECORDTYPE']")
    fld.clear()
    fld.send_keys(record_name)
    time.sleep(1)
    fld.send_keys("\n")  # Trigger dropdown selection
    time.sleep(3)
    print(f"🔎 Filter applied for '{record_name}'")

# ── Phase 3: Workflow Detail & Actions Extraction ──────────────────────────
def scrape_workflow_for_record(driver, record_name, results):
    """Click record row, then for each state click & extract actions."""
    # Click Name link in first result row
    row = driver.find_element(By.CSS_SELECTOR, "#div__body tbody tr uir-list-row-tr")
    link = row.find_element(By.CSS_SELECTOR, "a.dottedlink")
    link.click()
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "diagrammer")))
    time.sleep(2)

    workflow_name = driver.find_element(By.CSS_SELECTOR, "#workflow-title .name").text
    # Iterate states: click <g> with pointer-events
    states = driver.find_elements(By.CSS_SELECTOR, ".node-label")
    for state_label in states:
        state_label.click()
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, "panel-tab-switch-main")))
        driver.find_element(By.ID, "panel-tab-switch-main").click()
        time.sleep(1)

        category_elems = driver.find_elements(By.CSS_SELECTOR, ".category-row")
        for cat in category_elems:
            trigger = cat.find_element(By.XPATH, "./..//span[@class='trigger-row']").text
            actions = cat.find_elements(By.XPATH, ".//li[@class='action-row']")
            for act in actions:
                action_name = act.find_element(By.CSS_SELECTOR, "a.action-type").text
                args = act.find_element(By.CSS_SELECTOR, "span.action-arguments").text
                # Extract condition from onmouseover if present:
                cond = act.get_attribute("onmouseover") or ""
                results.append([
                    record_name,
                    workflow_name,
                    cat.text.split()[0],
                    trigger,
                    action_name,
                    args,
                    cond
                ])

def save_actions(results, filename="workflow_actions.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Record Type","Workflow","Category","Trigger","Action","Arguments","Condition"])
        writer.writerows(results)
    print(f"📂 Saved actions to {filename}")
