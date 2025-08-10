from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from config import HEADLESS_MODE

logger = logging.getLogger(__name__)

def switch_to_admin_role(driver, role_url):
    """Switch the current session to an administrator role, handling 2FA if necessary.

    Parameters
    ----------
    driver : selenium.webdriver
        The active WebDriver instance.
    role_url : str
        URL for switching to the desired role.
    """
    logger.info("➡️ Switching to admin role…")
    driver.get(role_url)

    if "loginchallenge/entry.nl" in getattr(driver, "current_url", ""):
        logger.info("🔐 2FA Authentication Required!")

        if HEADLESS_MODE:
            two_fa_code = input("🔢 Enter 2FA Code: ")
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "uif56_input"))
                )
                two_fa_input = driver.find_element(By.ID, "uif56_input")
                two_fa_input.send_keys(two_fa_code)

                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-type='primary'][role='button']"))
                )
                submit_button = driver.find_element(
                    By.CSS_SELECTOR, "div[data-type='primary'][role='button']"
                )
                driver.execute_script("arguments[0].click();", submit_button)
                logger.info("✅ 2FA Code Submitted.")
                time.sleep(5)
            except Exception as e:  # pragma: no cover - real browser failures
                logger.error(f"⚠️ Error entering 2FA code: {e}")
                driver.quit()
                return
        else:
            logger.info("⏳ Waiting for manual 2FA entry in the browser…")
            time.sleep(30)

    WebDriverWait(driver, 10).until(EC.url_contains("whence"))
    logger.info("🔄 Switched to admin role.")
