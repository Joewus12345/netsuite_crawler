from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from config import HEADLESS_MODE

logger = logging.getLogger(__name__)

def tick_remember_device_if_present(driver):
    """
    Attempts to tick NetSuite's remember/trust device checkbox if present.
    The exact text can vary by account/security setup.
    """
    try:
        checkbox = driver.execute_script(
            """
            const keywords = ["remember", "trust", "30 days", "do not ask"];

            const labels = [...document.querySelectorAll("label")];

            for (const label of labels) {
                const text = (label.textContent || "").trim().toLowerCase();

                if (!keywords.some(k => text.includes(k))) continue;

                const forId = label.getAttribute("for");

                if (forId) {
                    const input = document.getElementById(forId);
                    if (input) return input;
                }

                const wrapper = label.closest("div, span, td, tr");
                if (wrapper) {
                    const candidate = wrapper.querySelector(
                        "input[type='checkbox'], [role='checkbox']"
                    );
                    if (candidate) return candidate;
                }
            }

            return null;
            """
        )

        if not checkbox:
            return False

        checked = (
            checkbox.get_attribute("checked")
            or checkbox.get_attribute("aria-checked")
            or ""
        ).lower()

        if checked not in {"true", "checked"}:
            driver.execute_script(
                """
                const el = arguments[0];

                if (typeof el.click === "function") {
                    el.click();
                } else {
                    el.dispatchEvent(new MouseEvent("click", {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                }
                """,
                checkbox,
            )

        logger.info("☑️ Remember/trust device option selected if available.")
        return True

    except Exception as e:
        logger.warning(f"⚠️ Could not tick remember-device checkbox: {e}")
        return False


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
        
        tick_remember_device_if_present(driver)

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
            
            WebDriverWait(driver, 180).until(
                lambda d: "loginchallenge/entry.nl" not in d.current_url
            )

            logger.info("✅ Manual 2FA completed.")
            time.sleep(3)

    WebDriverWait(driver, 10).until(EC.url_contains("whence"))
    logger.info("🔄 Switched to admin role.")
