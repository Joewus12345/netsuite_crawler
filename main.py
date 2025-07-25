import argparse, json, sys
import logging
from selenium import webdriver
from crawler import login_netsuite
from config import HEADLESS_MODE

logging.basicConfig(level=logging.INFO, format="%(message)s")
import workflow_scraper as ws
import user_roles_scraper as urs

# ✅ Configure WebDriver (Allow headless mode)

# Set up WebDriver options
options = webdriver.ChromeOptions()
if HEADLESS_MODE:
    options.add_argument("--headless")  # Enable headless mode
    options.add_argument("--disable-gpu")  # Necessary for headless on Windows
    options.add_argument("--window-size=1920,1080")  # Set browser size
driver = webdriver.Chrome(options=options)
driver.maximize_window()

## ── Parse an optional --records list ───────────────────────────────────────
parser = argparse.ArgumentParser(
    description="Netsuite workflow scraper. Priority: --records → HARDCODED → dynamic extraction."
)
parser.add_argument(
    "--records",
    help="JSON list of record-type names to scrape, e.g. '[\"Admin Request\",\"Feedback\"]'",
    default=None
)
args = parser.parse_args()

# ✅ Start login process and ${crawling/navigation to Admin Item}
login_netsuite(driver)

# ── Decide which record‐types to scrape ────────────────────────────────────
HARDCODED = []

# Phase 1: Manually entered record types/HRA record types
# if args.records:
#     # 1) command‐line
#     try:
#         records = json.loads(args.records)
#         print(f"📝 Using command-line list: {records}")
#     except json.JSONDecodeError:
#         print("❌ Could not parse --records as JSON. Expecting a JSON array of strings.")
#         driver.quit()
#         sys.exit(1)

# elif HARDCODED:
#     # 2) hard-coded
#     records = HARDCODED
#     print(f"📝 Using hard-coded list: {records}")

# else:
#     # 3) dynamic extraction
#     print("📝 No manual list provided; extracting record types from NetSuite…")
#     ws.switch_to_hra_role(driver)
#     records = ws.extract_hra_record_types(driver)
#     print(f"📝 Dynamically extracted: {records}")

# Phase 1: User roles list & scrape
urs.switch_to_admin_role(driver)
urs.navigate_to_user_roles_list(driver)
results = urs.scrape_all_user_roles(driver)
urs.save_permissions(results)

# Phase 3: Scrape workflows
# all_actions = []
# for rec in records:
#     # always get back to the list first
#     ws.navigate_to_workflow_list(driver)
#     if ws.filter_by_record_type(driver, rec):
#         ws.scrape_workflow_for_record(driver, rec, all_actions)
#     else:
#         print(f"➡️ Skipping {rec}")

# ws.save_actions(all_actions)

# driver.quit()

# bash
# python main.py --records '["Admin Request","Feedback","Local Flight Request"]'

# powershell
# python main.py --records "[`"Admin Request`","`"Feedback`","`"Local Flight Request`"]"
# OR
# python main.py --records "[\"Admin Request\",\"Feedback\",\"Local Flight Request\"]"
