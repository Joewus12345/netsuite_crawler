from selenium import webdriver
from crawler import login_netsuite
from config import HEADLESS_MODE
import workflow_scraper as ws

# ✅ Configure WebDriver (Allow headless mode)
HEADLESS_MODE = HEADLESS_MODE # Set to True to run in headless mode

# Set up WebDriver options
options = webdriver.ChromeOptions()
if HEADLESS_MODE:
    options.add_argument("--headless")  # Enable headless mode
    options.add_argument("--disable-gpu")  # Necessary for headless on Windows
    options.add_argument("--window-size=1920,1080")  # Set browser size
driver = webdriver.Chrome(options=options)

# ✅ Start login process and ${crawling/navigation to Admin Item}
login_netsuite(driver)

# Phase 1: HRA record types
ws.switch_to_hra_role(driver)
records = ws.extract_hra_record_types(driver)

# Phase 2: Workflow list & filtering
ws.switch_to_admin_role(driver)
ws.navigate_to_workflow_list(driver)

# Phase 3: Scrape workflows
all_actions = []
for rec in records:
    # always get back to the list first
    ws.navigate_to_workflow_list(driver)
    if ws.filter_by_record_type(driver, rec):
        ws.scrape_workflow_for_record(driver, rec, all_actions)
    else:
        print(f"➡️ Skipping {rec}")

ws.save_actions(all_actions)

driver.quit()