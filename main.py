from selenium import webdriver
from crawler import login_netsuite
from config import HEADLESS_MODE

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
