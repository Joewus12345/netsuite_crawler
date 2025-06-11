from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import csv
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
    time.sleep(2)
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
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "uir-list-row-tr")))
    time.sleep(2)
    print("✅ On Workflow List page.")

def filter_by_record_type(driver, record_name):
    """Expand filters if needed, type record_name, then wait."""
    try:
        toggle = driver.find_element(By.CSS_SELECTOR, "span.ns-icon ns-filters-onoff-button")
        expanded = toggle.get_attribute("aria-expanded") == "true"
        if not expanded:
            toggle.click()
            WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input#inpt_Workflow_RECORDTYPE_1"))
            )
        else:
            # already expanded
            WebDriverWait(driver, 2).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input#inpt_Workflow_RECORDTYPE_1"))
            )
    except Exception as e:
        print(f"⚠️ Could not open filter pane for '{record_name}': {e}")
        return False

    try:
        fld = driver.find_element(By.CSS_SELECTOR, "input#inpt_Workflow_RECORDTYPE_1")
        fld.clear()
        fld.send_keys(record_name)
        time.sleep(1)
        fld.send_keys("\n")
        time.sleep(3)
        print(f"🔎 Filter applied for '{record_name}'")
        return True
    except Exception as e:
        print(f"⚠️ Error applying filter for '{record_name}': {e}")
        return False

# ── Phase 3: Workflow Detail & Actions Extraction ──────────────────────────
def scrape_workflow_for_record(driver, record_name, results):
    # avoid export buttons; click the Name link
    try:
        row = driver.find_element(By.CSS_SELECTOR, "#div__body tbody tr.uir-list-row-tr")
        link = row.find_element(By.CSS_SELECTOR, "td a.dottedlink")
        link.click()
    except Exception as e:
        print(f"⚠️ Couldn’t open workflow for '{record_name}': {e}")
        return

    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "diagrammer")))
    time.sleep(2)

    workflow_name = driver.find_element(By.CSS_SELECTOR, "#workflow-title .name").text

    # iterate each state node
    nodes = driver.find_elements(By.CSS_SELECTOR, "g[style*='pointer-events:visiblePainted']")
    for node in nodes:
        try:
            node.click()
        except:
            driver.execute_script("arguments[0].scrollIntoView(true);", node)
            node.click()

        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, "panel-tab-switch-main")))
        driver.find_element(By.ID, "panel-tab-switch-main").click()
        time.sleep(1)

        for cat in driver.find_elements(By.CSS_SELECTOR, ".category-row"):
            category_name = cat.text.strip()
            trigger = ""
            try:
                trigger = cat.find_element(
                    By.XPATH, "./following-sibling::ul//span[@class='trigger-row']"
                ).text
            except:
                pass

            actions = cat.find_elements(By.XPATH, "./following-sibling::ul//li[@class='action-row']")
            for act in actions:
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

def save_actions(results, filename="workflow_actions.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Record Type","Workflow","Category","Trigger","Action","Arguments","Condition"])
        writer.writerows(results)
    print(f"📂 Saved actions to {filename}")
