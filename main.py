from selenium import webdriver
from crawler import login_netsuite

# Set up WebDriver
options = webdriver.ChromeOptions()
# options.add_argument("--headless")  # Run in the background if needed
driver = webdriver.Chrome(options=options)

# ✅ Start login process and crawling
login_netsuite(driver)
