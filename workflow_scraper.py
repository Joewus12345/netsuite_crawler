from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException, ElementNotInteractableException
from selenium.webdriver.common.action_chains import ActionChains
import time
import csv
import json
import re
from bs4 import BeautifulSoup
from config import SECURITY_ANSWER, HEADLESS_MODE

HARDCODED: list[str] = []  # e.g., ["Admin Request", "Feedback"]

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

    # 3. try an exact (case‐sensitive) match
    match = next((opt for opt in options if opt["text"] == record_name), None)
    if not match:
        # 4. try case‐insensitive exact
        lower = record_name.lower()
        match = next((opt for opt in options if opt["text"].lower() == lower), None)

    # 5. try case‐insensitive 'startswith full record_name'
    if not match:
        match = next((opt for opt in options
                      if opt["text"].lower().startswith(record_name.lower())), None)

    # 6. fall back to multi-word prefixes (first N words, N decreasing)
    if not match:
        parts = record_name.split()
        # try prefixes of length len(parts)-1, len(parts)-2, ..., down to 2
        for L in range(len(parts)-1, 1, -1):
            prefix = " ".join(parts[:L]).lower()
            match = next((opt for opt in options if opt["text"].lower().startswith(prefix)), None)
            if match:
                print(f"⚠️ Fallback matched on prefix '{prefix}'")
                break

    # 7. *only now* fall back to single‐word prefix
    if not match:
        first = record_name.split()[0].lower()
        match = next((opt for opt in options if opt["text"].lower().startswith(first)), None)
        if match:
            print(f"⚠️ Last‐resort single‐word fallback matched on '{first}'")

    if not match:
        print(f"⚠️ No dropdown option matched '{record_name}'")
        return False

    # 8. set the hidden input and fire its onchange
    driver.execute_script("""
        let val = arguments[0];
        let input = document.getElementById('hddn_Workflow_RECORDTYPE_1');
        input.value = val;
        input.onchange();  // this will reload the grid for you
    """, match["value"])

    # 9. wait for either a data‐row or the “no data” cell
    WebDriverWait(driver, 10).until(lambda d:
        d.find_elements(By.CSS_SELECTOR, "tr.uir-list-row-tr") or
        d.find_elements(By.CSS_SELECTOR, "td.uir-nodata-cell")
    )

    # 10. detect “no data”
    if driver.find_elements(By.CSS_SELECTOR, "td.uir-nodata-cell"):
        print(f"➡️ No workflows for '{record_name}'")
        return False

    print(f"🔎 Filter applied via JS to '{match['text']}'")
    return True

#— helper to grab the last <span class="action-arguments"> or fall back into the onmouseover JS
def extract_action_arguments(act_el):
    # 1) try the visible span
    try:
        return act_el.find_element(By.CSS_SELECTOR, "span.action-arguments").text.strip()
    except NoSuchElementException:
        pass

    # 2) fallback: parse the onmouseover payload
    onmouse = act_el.get_attribute("onmouseover") or ""
    m = re.search(r"actionArguments:\s*'([^']*)'", onmouse)
    if m:
        return m.group(1).strip()

    # 3) nothing found
    return ""

def safe_find_text(base, by, selector, retries=3, delay=0.2):
    """
    Try up to `retries` times to find `.find_element(by, selector).text` on `base`,
    sleeping `delay` seconds between attempts. Returns "" on total failure.
    """
    for _ in range(retries):
        try:
            return base.find_element(by, selector).text
        except (StaleElementReferenceException, NoSuchElementException):
            time.sleep(delay)
    return ""

def safe_get_attr(base, attr, retries=2, delay=0.1):
    """
    Try up to `retries` times to read `base.get_attribute(attr)`,
    sleeping `delay` between attempts. Returns "" on failure.
    """
    for _ in range(retries):
        try:
            return base.get_attribute(attr) or ""
        except StaleElementReferenceException:
            time.sleep(delay)
    return ""

def reset_scroll_to_top(driver, max_clicks=30, pause=0.1, wait_timeout=5):
    """
    Click the ▲ button up to max_clicks times, then wait until
    the NetSuite scroll pane's scrollTop is 0 before returning.
    """
    try:
        up = driver.find_element(By.CSS_SELECTOR, ".yfiles-button-up")
    except NoSuchElementException:
        # no scrollbar present at all
        return

    # hammer the up arrow a few times
    for _ in range(max_clicks):
        try:
            up.click()
            time.sleep(pause)
        except ElementNotInteractableException:
            # we've reached the top
            break

    # now wait for scrollTop to become zero
    def scroll_at_top(drv):
        # returns True when pane.scrollTop == 0
        return drv.execute_script("""
            let pane = document.querySelector('#diagrammer .yfiles-scrollbar-range-vertical');
            return pane && pane.scrollTop === 0;
        """)

    try:
        WebDriverWait(driver, wait_timeout).until(scroll_at_top)
    except TimeoutException:
        # still give it one final nudge in JS
        driver.execute_script("""
            let pane = document.querySelector('#diagrammer .yfiles-scrollbar-range-vertical');
            if (pane) pane.scrollTop = 0;
        """)
        # no need to wait longer

def discover_all_states(driver, scroll_pause=0.3, max_rounds=50):
    """
    Scrolls the workflow canvas from top→bottom by bumping the scrollTop
    of the vertical pane.  Each time we collect whatever labels are in view
    and merge them into `seen`.  Returns the full map of (x,y)->label.
    """
    pane = driver.find_element(
        By.CSS_SELECTOR,
        "#diagrammer .yfiles-scrollbar-range-vertical"
    )

    seen = {}
    for round in range(max_rounds):
        # 1) collect whatever labels are in view right now
        labels = build_state_label_map(driver)
        new = {k: v for k, v in labels.items() if k not in seen}
        if not new and seen:
            # no new ones this round → we’ve reached the bottom
            break
        seen.update(new)

        # 2) scroll down one “page”
        driver.execute_script(
            "arguments[0].scrollTop += arguments[0].clientHeight * 0.9;",
            pane
        )
        time.sleep(scroll_pause)

    # finally, scroll back to top so that your subsequent clicks start
    # from the very top of the diagram
    driver.execute_script("arguments[0].scrollTop = 0;", pane)
    time.sleep(scroll_pause)

    return seen

def ensure_rect_visible(driver, raw_x, raw_y, max_scrolls=15):
    """
    Scroll the diagram vertically (via NetSuite's ▲▼ buttons) until
    the rect at (raw_x, raw_y) is within the SVG viewport, or until
    we've clicked max_scrolls times.
    Returns the now-visible WebElement, or raises if it never appeared.
    """
    # 1) Horizontal centering in case it's off to the side:
    rect_css = f"#diagrammer svg rect[x='{raw_x}'][y='{raw_y}']"
    try:
        # find once just to center horizontally
        r0 = driver.find_element(By.CSS_SELECTOR, rect_css)
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'})", r0
        )
    except NoSuchElementException:
        # if it's not in the DOM at all, bail
        raise RuntimeError(f"Could not find rect at ({raw_x},{raw_y}) in DOM")

    up_btn   = driver.find_element(By.CSS_SELECTOR, ".yfiles-button-up")
    down_btn = driver.find_element(By.CSS_SELECTOR, ".yfiles-button-down")

    def is_visible():
        try:
            r = driver.find_element(By.CSS_SELECTOR, rect_css)
            return driver.execute_script("""
                const el = arguments[0],
                      svg = document.querySelector("#diagrammer svg"),
                      eb = el.getBoundingClientRect(),
                      vb = svg.getBoundingClientRect();
                return eb.top >= vb.top && eb.bottom <= vb.bottom;
            """, r)
        except (StaleElementReferenceException, NoSuchElementException):
            return False

    # If already in view, return it
    if is_visible():
        return driver.find_element(By.CSS_SELECTOR, rect_css)

    # Otherwise, page down until we see it
    for _ in range(max_scrolls):
        down_btn.click()
        time.sleep(0.2)
        if is_visible():
            return driver.find_element(By.CSS_SELECTOR, rect_css)

    # Try paging up in case we overshot
    for _ in range(max_scrolls):
        up_btn.click()
        time.sleep(0.2)
        if is_visible():
            return driver.find_element(By.CSS_SELECTOR, rect_css)

    raise RuntimeError(f"Couldn’t scroll rect ({raw_x},{raw_y}) into view")

def build_state_label_map(driver):
    """
    Scans the #diagrammer SVG for every <rect> and its matching <g transform="translate(x y)">
    that contains the node-label text. Returns a dict keyed by (x,y) strings → text.
    """
    svg = driver.find_element(By.CSS_SELECTOR, "#diagrammer svg")
    label_map = {}
    # 1) First grab every <g> with a text inside
    for g in svg.find_elements(By.CSS_SELECTOR, "g"):
        tf = g.get_attribute("transform") or ""
        m = re.match(r"translate\(\s*([\d.]+)\s+([\d.]+)\s*\)", tf)
        if not m:
            continue
        # only consider g's that have a *direct* <text> child
        text_els = g.find_elements(By.CSS_SELECTOR, ":scope > text")
        if not text_els:
            continue

        x, y = m.groups()
        # now pull tspans only from that text element
        tspans = text_els[0].find_elements(By.TAG_NAME, "tspan")
        if not tspans:
            continue

        # join multiline labels
        text = " ".join(s.text.strip() for s in tspans if s.text.strip())
        # store under this key
        label_map[(x, y)] = text

    return label_map

# ── Phase 3: Workflow Detail & Actions Extraction ──────────────────────────
def scrape_workflow_for_record(driver, record_name, results):
    print(f"🔍 Starting scrape for '{record_name}'")
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

    # 3) Wait for the SVG canvas to become visible — retry up to 3 times
    svg_loaded = False
    for attempt in range(1, 4):
        try:
            # we wait *specifically* for a <rect> in the #diagrammer canvas:
            WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "#diagrammer svg"))
            )
            svg_loaded = True
            break
        except TimeoutException:
            print(f"⚠️ SVG didn’t appear for '{record_name}' (attempt {attempt}/3)")
            # go back and re-open this workflow record (in case it reloaded to login/etc)
            try:
                navigate_to_workflow_list(driver)
                # re-click into this same record
                row = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "tr.uir-list-row-tr"))
                )
                row.find_element(By.CSS_SELECTOR, "td:nth-child(2) a.dottedlink").click()
            except Exception:
                pass
    if not svg_loaded:
        print(f"➡️ Skipping '{record_name}' altogether — diagram never appeared.")
        # ensure we're back on the list before returning
        navigate_to_workflow_list(driver)
        return
    
    # **Now** that the SVG (and its scrollbar) is really there, reset to top
    reset_scroll_to_top(driver)
    # small buffer for the DOM to re-render at the new scroll position
    time.sleep(0.5)

    # grab the workflow's name for logging
    try:
        workflow_name = driver.find_element(By.CSS_SELECTOR, "#workflow-title .name").text
    except Exception:
        workflow_name = ""

    # Build the map once per workflow
    state_labels = discover_all_states(driver)

    # 4) Get the count of <rect> states
    # snapshot all coords
    coords = list(state_labels.keys())
    # sort by y then x, both numeric
    coords.sort(key=lambda t: (float(t[1]), float(t[0])))
    print(f"⚐ Found {len(coords)} total states")

    for idx, (x, y) in enumerate(coords, start=1):
        # grab its coords and lookup the name
        # x = rect.get_attribute("x")
        # y = rect.get_attribute("y")
        state_name = state_labels.get((x, y), "")
        print(f"→ State #{idx} at ({x},{y}) = “{state_name}”")

        # scroll it fully into view (even if it's in the overflow pane)
        rect = ensure_rect_visible(driver, x, y)
        if not rect:
            continue

        # 1) Capture old panel HTML (empty on first run)
        try:
            old_html = driver.find_element(
                By.CSS_SELECTOR, "#state-info-tab-actions"
            ).get_attribute("innerHTML")
        except NoSuchElementException:
            old_html = ""

        # 2) Click the state to open its panel
        ActionChains(driver).move_to_element(rect).click().perform()

        # 3) Wait until the panel appears *and* its HTML differs from old_html
        def panel_changed(drv):
            try:
                el = drv.find_element(By.CSS_SELECTOR, "#state-info-tab-actions")
                if el.get_attribute("innerHTML") != old_html:
                    return el
                return False
            except StaleElementReferenceException:
                # panel is mid-update—keep waiting
                return False

        try:
            panel = WebDriverWait(driver, 5).until(panel_changed)
            panel_html = panel.get_attribute("innerHTML")
        except TimeoutException:
            print(f"⚠️ Panel didn’t update for state #{idx}, skipping")
            # collapse back and move on
            driver.find_element(By.ID, "panel-tab-switch-workflow").click()
            continue

        if not panel_html:
            print(f"⚠️ Couldn’t stabilize Actions panel for '{record_name}', skipping state")
            # collapse back and continue
            driver.find_element(By.ID, "panel-tab-switch-workflow").click()
            continue

        # ————————— PARSE WITH BEAUTIFULSOUP —————————
        soup = BeautifulSoup(panel_html, "html.parser")

        # Loop categories
        for cat_li in soup.select("ul > li"):
            cat_label = cat_li.select_one("span.category-row")
            category_name = cat_label.get_text(strip=True) if cat_label else "<unnamed>"
            print(f"• Category: {category_name}")

            # Loop triggers
            for trig_li in cat_li.select(":scope > ul > li"):
                trig_label = trig_li.select_one("span.trigger-row")
                trigger_name = trig_label.get_text(strip=True) if trig_label else "<none>"
                print(f"↳ Trigger: {trigger_name}")

                # Loop actions
                for action_li in trig_li.select("ul > li.action-row"):
                    # action name
                    at = action_li.select_one("a.action-type")
                    name = at.get_text(strip=True) if at else "<no name>"

                    # arguments
                    arg_span = action_li.select_one("span.action-arguments")
                    if arg_span:
                        args = arg_span.get_text(strip=True)
                    else:
                        onmouse = action_li.get("onmouseover", "")
                        m = re.search(r"actionArguments:\s*'([^']*)'", onmouse)
                        args = m.group(1) if m else ""

                    # condition
                    cond = action_li.get("onmouseover", "")

                    print(f"↪ Action: {name} | args={args}")
                    results.append([
                        record_name,
                        workflow_name,
                        state_name,
                        category_name,
                        trigger_name,
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
    print(f"✅ Finished scrape for '{record_name}' ({len(coords)} states) \n")

    # 9) Finally go back to the workflow‐list for the next record
    navigate_to_workflow_list(driver)

def save_actions(results, filename="workflow_actions.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Record Type","Workflow","State","Category","Trigger","Action","Arguments","Condition"])
        writer.writerows(results)
    print(f"📂 Saved actions to {filename}")


def run(driver, records=None):
    """Orchestrate the workflow scraping process.

    Parameters
    ----------
    driver: selenium.webdriver
        Active WebDriver instance, already logged into NetSuite.
    records: list[str] | None
        Optional list of record type names.  When omitted, the list is
        extracted dynamically from the HRA role.
    """

    if records is None:
        if HARDCODED:
            records = HARDCODED
        else:
            switch_to_hra_role(driver)
            records = extract_hra_record_types(driver)

    switch_to_admin_role(driver)

    all_actions = []
    for rec in records:
        navigate_to_workflow_list(driver)
        if filter_by_record_type(driver, rec):
            scrape_workflow_for_record(driver, rec, all_actions)
        else:
            print(f"➡️ Skipping {rec}")

    save_actions(all_actions)
