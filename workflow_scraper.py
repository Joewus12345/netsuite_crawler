from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains
import time
import csv
import json
from config import SECURITY_ANSWER, HEADLESS_MODE

# ── Phase 1: HRA Record Types Extraction ────────────────────────────────────
def switch_to_hra_role(driver):
    url = "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~18903~1059~N"
    driver.get(url)

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

    WebDriverWait(driver, 10).until(EC.url_contains("center/card.nl"))
    print("🔄 Switched to HRA role.")

def extract_hra_record_types(driver):
    driver.get("https://4891605.app.netsuite.com/app/center/card.nl?sc=13&whence=")
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CLASS_NAME, "ns-link-button")))
    driver.find_element(By.CLASS_NAME, "ns-link-button").click()
    
    # Wait until at least one record‐type link becomes visible:
    WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "a.ns-searchable-value[target='_self']"))
    )

    elems = driver.find_elements(By.CSS_SELECTOR, "a.ns-searchable-value[target='_self']")
    names = [e.text.strip() for e in elems]
    print(f"✅ HRA record types: {names}")
    return names

# ── Phase 2: Workflow List Navigation & Filter ─────────────────────────────
def switch_to_admin_role(driver):
    url = "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~18903~1114~N"
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

def navigate_to_workflow_list(driver):
    driver.get("https://4891605.app.netsuite.com/app/common/workflow/setup/workflowlist.nl?whence=")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "div__footer")))
    print("✅ On Workflow List page.")

def filter_by_record_type(driver, record_name):
    """Open the Record Type dropdown, pick the closest match, then wait for the grid."""
    # 1. open the filter pane if it isn’t already
    toggle = WebDriverWait(driver, 5).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "span.ns-icon.ns-filters-onoff-button"))
    )
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input#inpt_Workflow_RECORDTYPE_1"))
        )

    # 2. grab the ns-dropdown and parse its JSON
    dd = driver.find_element(
        By.CSS_SELECTOR,
        "div.ns-dropdown[data-name='Workflow_RECORDTYPE']"
    )
    options = json.loads(
        dd.get_attribute("data-options")
          .replace('&quot;', '"')  # un-HTML-encode
    )

    # 3. find the “exact” or prefix match
    match = None
    for opt in options:
        if opt["text"] == record_name:
            match = opt
            break

    # 4. otherwise, split on spaces and try each word
    if not match:
        first_word = record_name.split()[0].lower()
        for opt in options:
            if opt.text.strip().lower().startswith(first_word):
                match = opt
                break

    if not match:
        print(f"⚠️ No dropdown option matched '{record_name}'")
        return False

    # 5. set the hidden input and fire its onchange
    driver.execute_script("""
        let val = arguments[0];
        let input = document.getElementById('hddn_Workflow_RECORDTYPE_1');
        input.value = val;
        input.onchange();  // this will reload the grid for you
    """, match["value"])

    # 6. wait for either a data‐row or the “no data” cell
    WebDriverWait(driver, 10).until(lambda d:
        d.find_elements(By.CSS_SELECTOR, "tr.uir-list-row-tr") or
        d.find_elements(By.CSS_SELECTOR, "td.uir-nodata-cell")
    )

    # 7. detect “no data”
    if driver.find_elements(By.CSS_SELECTOR, "td.uir-nodata-cell"):
        print(f"➡️ No workflows for '{record_name}'")
        return False

    print(f"🔎 Filter applied via JS to '{match['text']}'")
    return True

# ── Phase 3: Workflow Detail & Actions Extraction ──────────────────────────
def scrape_workflow_for_record(driver, record_name, results):
    # 1) Open the workflow (Name link → maybe View button)
    try:
        row = driver.find_element(By.CSS_SELECTOR, "tr.uir-list-row-tr")
        row.find_element(By.CSS_SELECTOR, "td:nth-child(2) a.dottedlink").click()
    except:
        try:
            row = driver.find_element(By.CSS_SELECTOR, "tr.uir-list-row-tr")
            row.find_element(By.CSS_SELECTOR, "td:nth-child(1) a.dottedlink").click()
            WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input#view.rndbuttoninpt.bntBgT"))
            ).click()
        except Exception as e:
            print(f"⚠️ open fail “{record_name}”: {e}")
            return

    # 2) Make sure the Workflow tab is active
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "panel-tab-switch-workflow"))
        ).click()
    except TimeoutException:
        pass

    # 3) Wait for the SVG canvas to become visible
    WebDriverWait(driver, 15).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "#diagrammer svg"))
    )
    workflow_name = driver.find_element(By.CSS_SELECTOR, "#workflow-title .name").text

    # 4) Get the count of <rect> states
    rect_count = len(driver.find_elements(By.CSS_SELECTOR, "#diagrammer svg rect"))
    for idx in range(rect_count):
        # re-find to avoid stale references
        rects = driver.find_elements(By.CSS_SELECTOR, "#diagrammer svg rect")
        rect = rects[idx]

        # scroll it into view
        driver.execute_script("arguments[0].scrollIntoView(true);", rect)

        # click via ActionChains and retry on stale/blocked
        for attempt in range(3):
            try:
                ActionChains(driver).move_to_element(rect).click().perform()
                break
            except (StaleElementReferenceException, Exception):
                time.sleep(0.5)
                rects = driver.find_elements(By.CSS_SELECTOR, "#diagrammer svg rect")
                rect = rects[idx]
        else:
            print(f"⚠️ Could not click state #{idx+1} for '{record_name}'")
            continue

        # 5) Switch into the “State” panel
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "panel-tab-switch-main"))
        ).click()

        # 6) Wait for at least one category‐row
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "#panel-tab-main.tab.active .category-row"))
        )

        # 7) Scrape that state’s actions
        for cat in driver.find_elements(By.CSS_SELECTOR, "#panel-tab-main.tab.active .category-row"):
            category_name = cat.text.splitlines()[0]
            try:
                trigger = cat.find_element(By.CSS_SELECTOR, "span.trigger-row").text
            except:
                trigger = ""
            for act in cat.find_elements(By.CSS_SELECTOR, "li.action-row"):
                name = act.find_element(By.CSS_SELECTOR, "a.action-type").text
                args = act.find_element(By.CSS_SELECTOR, "span.action-arguments").text
                cond = act.get_attribute("onmouseover") or ""
                results.append([
                    record_name,
                    workflow_name,
                    category_name,
                    trigger,
                    name,
                    args,
                    cond
                ])

        # 8) Collapse State panel and go back to Workflow canvas
        try:
            driver.find_element(By.ID, "panel-tab-switch-workflow").click()
            time.sleep(0.3)
        except:
            pass

    # 9) Finally go back to the workflow‐list for the next record
    navigate_to_workflow_list(driver)

def save_actions(results, filename="workflow_actions.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Record Type","Workflow","Category","Trigger","Action","Arguments","Condition"])
        writer.writerows(results)
    print(f"📂 Saved actions to {filename}")
