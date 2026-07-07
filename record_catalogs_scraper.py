from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    WebDriverException,
)
import csv
import logging
import math
import os
import time
from datetime import datetime

from auth_utils import switch_to_admin_role as _switch_to_admin_role


logger = logging.getLogger(__name__)

ADMIN_ROLE_URL = (
    "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?"
    "id=4891605~9203~1073~N"
)

RECORD_CATALOG_URL = (
    "https://4891605.app.netsuite.com/app/recordscatalog/rcbrowser.nl?whence="
)

TREE_ROOT = '[data-automation-id="RecordSearchResults"]'
TREE_CONTAINER = f'{TREE_ROOT} [data-widget="VirtualTreeContainer"]'
TREE_ITEM = 'li[role="treeitem"][data-widget="TreeItem"]'

FIELDS_GRID = '[data-automation-id="SSAnalyticAPIFieldsDataGrid"]'
JOINS_GRID = '[data-automation-id="SSAnalyticAPIJoinsDataGrid"]'

# Many helper functions below use GRID as the currently active NetSuite grid.
# We switch it between Fields and Joins before scraping each tab.
GRID = FIELDS_GRID
GRID_VIEW = f'{GRID} [data-widget="GridView"]'
GRID_VIEWPORT = f'{GRID} [data-grid-view-section="viewport"]'
GRID_BODY_ROWS = f'{GRID} [data-widget="GridRowSegment"][data-row-type="data"]'


def set_active_grid(grid_css):
    """Switch all generic grid helpers to the selected Record Catalog grid."""
    global GRID, GRID_VIEW, GRID_VIEWPORT, GRID_BODY_ROWS
    GRID = grid_css
    GRID_VIEW = f'{GRID} [data-widget="GridView"]'
    GRID_VIEWPORT = f'{GRID} [data-grid-view-section="viewport"]'
    GRID_BODY_ROWS = f'{GRID} [data-widget="GridRowSegment"][data-row-type="data"]'


def get_visible_grid_now(driver, grid_css=None):
    """
    Returns the visible active Fields/Joins grid, not a hidden stale grid.

    NetSuite can keep the previous tab's grid in the DOM after switching
    between Fields and Joins. Using document.querySelector() or Selenium's
    first matching element can therefore read the wrong hidden grid.
    """
    css = grid_css or GRID
    try:
        return driver.execute_script(
            """
            const css = arguments[0];
            const grids = [...document.querySelectorAll(css)];

            for (const grid of grids) {
                const rect = grid.getBoundingClientRect();
                const style = window.getComputedStyle(grid);

                const visible =
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    style.opacity !== "0";

                if (visible) return grid;
            }

            return null;
            """,
            css,
        )
    except WebDriverException:
        return None

TREE_ROW_HEIGHT = 34

# Set this to a small number like 10 while testing.
# Change to None when you are ready to scrape everything.
# TEST_LIMIT = 10
TEST_LIMIT = None

# During long runs, write checkpoint files so a browser/session crash does not
# wipe out all rows already scraped.
CHECKPOINT_EVERY = 50
# V2 outputs are intentionally separate from the earlier files because
# enabling "Show unavailable items" changes the record count and the content.
PARTIAL_FIELDS_FILE = "record_catalogs_fields_v2.partial.csv"
PARTIAL_JOINS_FILE = "record_catalogs_joins_v2.partial.csv"
STATUS_FILE = "record_catalogs_status_v2.csv"

FINAL_FIELDS_FILE = "record_catalogs_fields_v2.csv"
FINAL_JOINS_FILE = "record_catalogs_joins_v2.csv"

# Retry policy:
# - Expand/click failures get up to 2 retries after the first attempt.
# - Records with no catalog tables are treated as verified 0-field/0-join records after verification.
# - Fields + Joins grid scraping gets one retry after the first attempt.
EXPAND_CLICK_MAX_ATTEMPTS = 3
GRID_SCRAPE_MAX_ATTEMPTS = 2

# Resume policy:
# - Keep the V2 partial CSVs and V2 status CSV in the same folder.
# - On the next run, the scraper loads them, skips records already marked done,
#   and continues with failed/unseen records.
# - IMPORTANT: old no_fields_grid rows are NOT treated as done anymore because
#   they can be false positives caused by slow NetSuite loading/connectivity.
RESUME_FROM_CHECKPOINT = True

# Old `no_fields_grid` rows are not final; V2 uses a fresh status file.
# Only `verified_no_catalog_tables` is considered final/no-fields/no-joins.
DONE_STATUSES = {"success", "verified_no_catalog_tables", "skipped_missing_name"}

# If a record seems to have no fields, verify that conclusion with longer waits
# before writing `verified_no_catalog_tables`.
NO_FIELDS_GRID_VERIFY_ATTEMPTS = 3
NO_FIELDS_GRID_VERIFY_TIMEOUTS = [12, 25, 45]
NO_FIELDS_GRID_RECHECK_PAUSE = 2.5

FIELDNAMES = [
    "Record Name",
    "Record ID",
    "Field ID",
    "Name",
    "Type",
    "Available",
    "Feature",
    "Permission",
    "Join",
    "Is Subfield",
    "Parent Field ID",
    "Field Path",
    "Nested Record ID",
    "Nested Record Name",
]

JOIN_FIELDNAMES = [
    "Record Name",
    "Record ID",
    "Category Name",
    "Category ID",
    "Join Type",
    "Join Kind",
    "Target Name",
    "Target Record ID",
    "Source Field ID",
    "Cardinality",
    "Available",
    "Condition",
    "Is Subjoin",
    "Parent Source Field ID",
    "Join Path",
]

STATUS_FIELDNAMES = [
    "Record Index",
    "Record Name",
    "Record ID",
    "Status",
    "Field Count",
    "Join Count",
    "Attempts",
    "Error",
    "Timestamp",
]


def switch_to_admin_role(driver):
    _switch_to_admin_role(driver, ADMIN_ROLE_URL)


def navigate_to_record_catalog(driver):
    logger.info("➡️ Navigating to Record Catalog…")
    driver.get(RECORD_CATALOG_URL)

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, TREE_ROOT))
    )
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, TREE_CONTAINER))
    )

    logger.info("✅ On Record Catalog page.")


def ensure_show_unavailable_items(driver, timeout=15):
    """
    Ticks the "Show unavailable items" checkbox before counting/scraping records.

    This must happen before get_total_records(), because NetSuite's left tree
    can expose additional record types only after this checkbox is enabled.
    """
    logger.info("☑️ Ensuring 'Show unavailable items' is checked…")

    def find_checkbox(drv):
        return drv.execute_script(
            """
            const labels = [...document.querySelectorAll("label")];
            const label = labels.find(l =>
                (l.textContent || "").trim().toLowerCase() === "show unavailable items"
            );

            if (!label) return null;

            const forId = label.getAttribute("for");
            if (forId) {
                const direct = document.getElementById(forId);
                if (direct) return direct;
            }

            if (label.id) {
                const labelled = document.querySelector(`[role="checkbox"][aria-labelledby="${label.id}"]`);
                if (labelled) return labelled;
            }

            const wrapper = label.closest('[data-widget="CheckBox"]');
            return wrapper ? wrapper.querySelector('[role="checkbox"]') : null;
            """
        )

    checkbox = WebDriverWait(driver, timeout).until(find_checkbox)

    if (checkbox.get_attribute("aria-checked") or "").lower() != "true":
        safe_click(driver, checkbox)

        WebDriverWait(driver, timeout).until(
            lambda d: (
                find_checkbox(d).get_attribute("aria-checked") or ""
            ).lower() == "true"
        )

        # Let the virtual tree rebuild after the filter changes.
        time.sleep(1.5)
    else:
        logger.info("☑️ 'Show unavailable items' was already checked.")

    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, TREE_CONTAINER))
    )


def find_right_catalog_tab(driver, tab_name):
    """
    Finds a tab from the right-side Record Catalog detail panel only.

    The page has two different tab groups:
    - left panel: Records / Fields
    - right detail panel: Overview / Fields / Joins

    This helper intentionally ignores the left panel so selecting "Fields"
    never hides the Records tree.
    """
    tab_name_lower = tab_name.strip().lower()

    return driver.execute_script(
        """
        const wanted = arguments[0];

        function tabLabel(tab) {
            const titleNode = tab.querySelector('[title]');
            const title = titleNode ? (titleNode.getAttribute('title') || '').trim() : '';
            const text = (tab.textContent || '').trim();
            return (title || text).toLowerCase();
        }

        const tablists = [...document.querySelectorAll('[role="tablist"]')];

        for (const list of tablists) {
            const tabs = [...list.querySelectorAll('[role="tab"]')];
            const labels = tabs.map(tabLabel).filter(Boolean);

            const isRightDetailTabs =
                labels.includes('overview') &&
                labels.includes('fields') &&
                labels.includes('joins');

            if (!isRightDetailTabs) continue;

            for (const tab of tabs) {
                if (tabLabel(tab) === wanted) {
                    return tab;
                }
            }
        }

        return null;
        """,
        tab_name_lower,
    )


def ensure_left_records_tab(driver, timeout=10):
    """
    Keeps the left panel on the Records tab.

    This is a safety net in case a previous run or manual interaction left the
    side panel on its own Fields tab, which hides the RecordSearchResults tree.
    """
    def find_records_tab(drv):
        return drv.execute_script(
            """
            function tabLabel(tab) {
                const titleNode = tab.querySelector('[title]');
                const title = titleNode ? (titleNode.getAttribute('title') || '').trim() : '';
                const text = (tab.textContent || '').trim();
                return (title || text).toLowerCase();
            }

            const tablists = [...document.querySelectorAll('[role="tablist"]')];

            for (const list of tablists) {
                const tabs = [...list.querySelectorAll('[role="tab"]')];
                const labels = tabs.map(tabLabel).filter(Boolean);

                const isLeftRecordsTabs =
                    labels.includes('records') &&
                    labels.includes('fields') &&
                    !labels.includes('overview') &&
                    !labels.includes('joins');

                if (!isLeftRecordsTabs) continue;

                for (const tab of tabs) {
                    if (tabLabel(tab) === 'records') {
                        return tab;
                    }
                }
            }

            return null;
            """
        )

    try:
        tab = WebDriverWait(driver, timeout).until(find_records_tab)
        if (tab.get_attribute("aria-selected") or "").lower() != "true":
            safe_click(driver, tab)
            WebDriverWait(driver, timeout).until(
                lambda d: (
                    find_records_tab(d).get_attribute("aria-selected") or ""
                ).lower() == "true"
            )
            time.sleep(0.35)
    except TimeoutException:
        # Some NetSuite layouts may not expose this tab group immediately.
        # Do not fail here; scroll_tree_to_index will still fail loudly if the
        # record tree is genuinely unavailable.
        pass


def select_catalog_tab(driver, tab_name, timeout=15):
    """
    Selects one of the right-side detail tabs: Overview, Fields, or Joins.

    This deliberately scopes tab selection to the tablist containing all three
    right-side detail tabs. It must not click the left-side Records/Fields tabs.
    """
    tab_name_lower = tab_name.strip().lower()

    def find_tab(drv):
        return find_right_catalog_tab(drv, tab_name_lower) or False

    tab = WebDriverWait(driver, timeout).until(find_tab)

    if (tab.get_attribute("aria-selected") or "").lower() != "true":
        safe_click(driver, tab)

    WebDriverWait(driver, timeout).until(
        lambda d: (
            find_right_catalog_tab(d, tab_name_lower).get_attribute("aria-selected") or ""
        ).lower() == "true"
    )

    # Ensure our right-side tab click did not disturb the left-side tree.
    ensure_left_records_tab(driver, timeout=5)

    time.sleep(0.35)
    return tab

def safe_click(driver, element):
    """
    More defensive click helper.

    NetSuite sometimes exposes SVG nodes or wrapped UI elements where JS
    element.click() is not available. Dispatching a real MouseEvent is safer
    than blindly calling arguments[0].click().
    """
    try:
        element.click()
        return
    except (
        ElementClickInterceptedException,
        ElementNotInteractableException,
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    try:
        driver.execute_script(
            """
            const el = arguments[0];
            if (!el) {
                throw new Error("safe_click received a null element");
            }

            if (typeof el.click === "function") {
                el.click();
                return;
            }

            el.dispatchEvent(new MouseEvent("click", {
                bubbles: true,
                cancelable: true,
                view: window
            }));
            """,
            element,
        )
        return
    except WebDriverException:
        # Final fallback: use Selenium mouse movement/click.
        ActionChains(driver).move_to_element(element).click().perform()

def set_scroll_top(driver, element, top):
    """
    NetSuite's tree is virtualized, so we scroll the virtual container directly
    and fire a scroll event to force it to render the next batch of records.
    """
    driver.execute_script(
        """
        const el = arguments[0];
        const top = arguments[1];

        el.scrollTop = top;
        el.dispatchEvent(new Event('scroll', { bubbles: true }));
        """,
        element,
        top,
    )
    time.sleep(0.35)


def get_tree_container(driver):
    return WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, TREE_CONTAINER))
    )


def get_total_records(driver):
    """
    Reads aria-setsize from any visible top-level record.
    Your pasted HTML shows aria-setsize="2040".
    """
    item = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, f'{TREE_ITEM}[aria-level="0"][aria-setsize]')
        )
    )

    raw_total = item.get_attribute("aria-setsize")
    try:
        return int(raw_total)
    except (TypeError, ValueError):
        logger.warning("⚠️ Could not read total record count; falling back to 2000.")
        return 2000


def scroll_tree_to_index(driver, index):
    container = get_tree_container(driver)
    set_scroll_top(driver, container, index * TREE_ROW_HEIGHT)

    selector = f'{TREE_ITEM}[aria-level="0"][data-index="{index}"]'

    return WebDriverWait(driver, 10).until(
        lambda d: next(
            (
                item
                for item in d.find_elements(By.CSS_SELECTOR, selector)
                if item.is_displayed()
            ),
            False,
        )
    )


def extract_record_identity(record_item):
    """
    Top-level record rows usually have:
    - first text span: display name
    - second text span: script/internal id

    For [Missing Label:...] records, the second span may be blank in some cases,
    so we derive the internal id from the missing-label path.
    """
    spans = record_item.find_elements(
        By.CSS_SELECTOR,
        '[data-tree-section="content"] span[data-widget="Text"]',
    )
    texts = [span.text.strip() for span in spans if span.text.strip()]

    record_name = texts[0] if texts else ""
    record_id = texts[1] if len(texts) > 1 else ""

    if not record_id and record_name.startswith("[Missing Label:"):
        cleaned = record_name.strip("[]")
        record_id = cleaned.split(".")[-1]

    return record_name, record_id


def expand_record(driver, record_item):
    """
    Expands one top-level record so the child 'SuiteScript and REST Query API'
    appears underneath.
    """
    parent_id = record_item.get_attribute("id")

    if record_item.get_attribute("aria-expanded") != "true":
        expander = record_item.find_element(
            By.CSS_SELECTOR,
            '[data-tree-section="expander"]',
        )
        safe_click(driver, expander)

    child_selector = (
        f'{TREE_ITEM}[aria-level="1"][data-parent-item-id="{parent_id}"]'
    )

    child = WebDriverWait(driver, 10).until(
        lambda d: next(
            (
                item
                for item in d.find_elements(By.CSS_SELECTOR, child_selector)
                if "SuiteScript and REST Query API" in item.text
            ),
            False,
        )
    )

    return parent_id, child


def collapse_record(driver, parent_id):
    """
    Collapse after each scrape so data-index scrolling remains predictable.
    """
    try:
        parent = driver.find_element(By.ID, parent_id)
        if parent.get_attribute("aria-expanded") == "true":
            expander = parent.find_element(
                By.CSS_SELECTOR,
                '[data-tree-section="expander"]',
            )
            safe_click(driver, expander)
            time.sleep(0.15)
    except Exception:
        pass


def get_grid(driver, grid_css=None, timeout=15):
    css = grid_css or GRID
    return WebDriverWait(driver, timeout).until(
        lambda d: get_visible_grid_now(d, css) or False
    )



def grid_signature(driver):
    """
    Returns a lightweight signature of the visible active grid so we can tell
    whether NetSuite has actually replaced/refreshed it.
    """
    try:
        grid = get_visible_grid_now(driver, GRID)
        if not grid:
            return ""

        rows = grid.find_elements(
            By.CSS_SELECTOR,
            '[data-widget="GridRowSegment"]',
        )

        parts = []
        for row in rows[:20]:
            parts.append(
                "|".join([
                    row.get_attribute("data-row-id") or "",
                    row.get_attribute("data-row-type") or "",
                    row.get_attribute("data-index") or "",
                    row.text.strip()[:80],
                ])
            )

        return "||".join(parts)
    except Exception:
        return ""


def record_tokens(record_name, record_id):
    tokens = []

    for value in [record_id, record_name]:
        value = (value or "").strip()
        if value:
            tokens.append(value)

        if value.startswith("[Missing Label:"):
            tokens.append(value.strip("[]").split(".")[-1])

    return [t for t in dict.fromkeys(tokens) if t]


def wait_for_grid_to_match_record(driver, record_name, record_id, old_signature="", timeout=25):
    """
    Wait until the visible right-side Fields grid belongs to the selected record,
    not the previous record or the hidden Joins tab.
    """
    tokens = record_tokens(record_name, record_id)

    def ready(drv):
        try:
            grid = get_visible_grid_now(drv, GRID)
            if not grid:
                return False

            text = grid.text.strip()
            sig = grid_signature(drv)

            if old_signature and sig == old_signature:
                return False

            if not text:
                return False

            # Best case: synthetic header contains the record id/name.
            if any(token in text for token in tokens):
                return grid

            # Fallback: if the visible grid signature changed and rows exist,
            # allow it, but only after a real DOM change.
            data_rows = grid.find_elements(
                By.CSS_SELECTOR,
                '[data-widget="GridRowSegment"][data-row-type="data"]',
            )
            if sig and sig != old_signature and data_rows:
                return grid

            return False

        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            return False

    return WebDriverWait(driver, timeout).until(ready)


def get_grid_scroll_box(driver, axis="y"):
    """
    Finds the actual scrollable element inside the visible active NetSuite grid.
    Do not assume [data-grid-view-section="viewport"] is always the scroll box.
    """
    grid = get_grid(driver)

    script = """
        const grid = arguments[0];
        const axis = arguments[1];

        if (!grid) return null;

        const nodes = [grid, ...grid.querySelectorAll("*")];

        let best = null;
        let bestRange = 0;

        for (const el of nodes) {
            const rect = el.getBoundingClientRect();
            if (rect.width < 20 || rect.height < 20) continue;

            const range = axis === "y"
                ? el.scrollHeight - el.clientHeight
                : el.scrollWidth - el.clientWidth;

            if (range > bestRange) {
                best = el;
                bestRange = range;
            }
        }

        return best || grid.querySelector('[data-widget="GridView"]') || grid;
    """

    box = driver.execute_script(script, grid, axis)
    return box or grid


def visible_grid_row_indexes(driver):
    try:
        grid = get_visible_grid_now(driver, GRID)
        if not grid:
            return []

        rows = grid.find_elements(
            By.CSS_SELECTOR,
            '[data-widget="GridRowSegment"][data-row-type="data"]',
        )

        indexes = []
        for row in rows:
            raw = row.get_attribute("data-index")
            if raw is not None:
                try:
                    indexes.append(int(raw))
                except ValueError:
                    pass

        return sorted(set(indexes))
    except Exception:
        return []


def reset_grid_scroll(driver, timeout=10):
    """
    Reset both horizontal and vertical scrolls.
    Then wait until the first visible data row is row 1, or until we confirm
    there is no vertical scrolling needed.
    """
    y_box = get_grid_scroll_box(driver, "y")
    x_box = get_grid_scroll_box(driver, "x")

    driver.execute_script(
        """
        const y = arguments[0];
        const x = arguments[1];

        if (y) {
            y.scrollTop = 0;
            y.dispatchEvent(new Event("scroll", { bubbles: true }));
            y.dispatchEvent(new WheelEvent("wheel", {
                deltaY: -1000,
                bubbles: true,
                cancelable: true
            }));
        }

        if (x) {
            x.scrollLeft = 0;
            x.dispatchEvent(new Event("scroll", { bubbles: true }));
        }
        """,
        y_box,
        x_box,
    )

    def top_ready(drv):
        try:
            info = drv.execute_script(
                """
                const el = arguments[0];
                return {
                    top: el ? el.scrollTop : 0,
                    range: el ? (el.scrollHeight - el.clientHeight) : 0
                };
                """,
                y_box,
            )

            indexes = visible_grid_row_indexes(drv)

            if info["range"] <= 5:
                return True

            return info["top"] <= 2 and (not indexes or min(indexes) <= 1)

        except StaleElementReferenceException:
            return False

    try:
        WebDriverWait(driver, timeout).until(top_ready)
    except TimeoutException:
        # Do not crash. We will still scrape whatever becomes visible.
        pass

    time.sleep(0.4)


def scroll_grid_down(driver):
    """
    Scrolls the actual grid scroll container, not just the page.
    Also fires wheel/scroll events because NetSuite UI components often listen
    to synthetic scroll events.
    """
    y_box = get_grid_scroll_box(driver, "y")

    return driver.execute_script(
        """
        const el = arguments[0];

        const before = el.scrollTop;
        const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
        const step = Math.max(180, Math.floor(el.clientHeight * 0.75));

        el.scrollTop = Math.min(maxTop, before + step);

        el.dispatchEvent(new Event("scroll", { bubbles: true }));
        el.dispatchEvent(new WheelEvent("wheel", {
            deltaY: step,
            bubbles: true,
            cancelable: true
        }));

        return {
            before,
            after: el.scrollTop,
            maxTop,
            clientHeight: el.clientHeight,
            scrollHeight: el.scrollHeight,
            atBottom: el.scrollTop >= maxTop - 3
        };
        """,
        y_box,
    )


def get_grid_viewport(driver):
    """
    The visible viewport inside the active NetSuite Record Catalog grid.
    Native wheel scrolling should target this element.
    """
    grid = get_grid(driver)
    return WebDriverWait(driver, 15).until(
        lambda d: next(
            (
                vp for vp in grid.find_elements(
                    By.CSS_SELECTOR,
                    '[data-grid-view-section="viewport"]'
                )
                if vp.is_displayed()
            ),
            False,
        )
    )


def focus_fields_grid(driver):
    """
    Focus the grid before keyboard/wheel scrolling.
    """
    grid = get_grid(driver)

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        grid,
    )

    try:
        ActionChains(driver).move_to_element(grid).click().perform()
    except Exception:
        driver.execute_script("arguments[0].focus();", grid)

    time.sleep(0.2)
    return grid


def native_wheel_grid(driver, delta_y, pause=0.45):
    """
    Sends a real browser wheel event over the NetSuite grid viewport.
    This is closer to what happens when you manually scroll the table.
    """
    viewport = get_grid_viewport(driver)

    try:
        origin = ScrollOrigin.from_element(viewport)
        ActionChains(driver).scroll_from_origin(origin, 0, delta_y).perform()
    except Exception:
        # Fallback for Selenium/browser combinations where wheel input fails.
        driver.execute_script(
            """
            const el = arguments[0];
            const dy = arguments[1];

            el.dispatchEvent(new WheelEvent("wheel", {
                deltaY: dy,
                deltaMode: 0,
                bubbles: true,
                cancelable: true
            }));

            const candidates = [el, ...el.querySelectorAll("*")];
            const scrollable = candidates.find(x => x.scrollHeight > x.clientHeight + 5);

            if (scrollable) {
                scrollable.scrollTop += dy;
                scrollable.dispatchEvent(new Event("scroll", { bubbles: true }));
            }
            """,
            viewport,
            delta_y,
        )

    time.sleep(pause)


def wait_for_visible_indexes_change(driver, old_indexes, timeout=6):
    """
    Wait until NetSuite renders a different row range after scrolling.
    """
    old_tuple = tuple(old_indexes)

    try:
        WebDriverWait(driver, timeout).until(
            lambda drv: tuple(visible_grid_row_indexes(drv)) != old_tuple
        )
        return True
    except TimeoutException:
        return False


def reset_fields_grid_to_top(driver, max_attempts=12):
    """
    Move the active grid to the top using real keyboard/wheel input.
    Works for both Fields and Joins because it uses the active GRID global.
    """
    focus_fields_grid(driver)

    for _ in range(max_attempts):
        indexes = visible_grid_row_indexes(driver)

        try:
            y_box = get_grid_scroll_box(driver, "y")
            scroll_info = driver.execute_script(
                """
                const el = arguments[0];
                return {
                    top: el ? el.scrollTop : 0,
                    range: el ? Math.max(0, el.scrollHeight - el.clientHeight) : 0
                };
                """,
                y_box,
            )
        except Exception:
            scroll_info = {"top": 0, "range": 0}

        if indexes and min(indexes) <= 1:
            break

        # Empty or fully visible grid: nothing meaningful to reset.
        if not indexes and scroll_info.get("range", 0) <= 5:
            break

        if scroll_info.get("top", 0) <= 2 and not indexes:
            break

        try:
            ActionChains(driver).send_keys(Keys.HOME).perform()
        except Exception:
            pass

        native_wheel_grid(driver, -3000, pause=0.25)

    wait_for_grid_stable(driver, stable_for=0.6, timeout=8)


def scroll_fields_grid_down(driver):
    """
    Scroll down inside the fields grid and verify whether the rendered row
    indexes changed.
    """
    focus_fields_grid(driver)

    before_indexes = visible_grid_row_indexes(driver)

    # First try native wheel. This is the most important part.
    native_wheel_grid(driver, 650, pause=0.45)

    changed = wait_for_visible_indexes_change(driver, before_indexes, timeout=5)

    if not changed:
        # Fallback: PageDown sometimes triggers NetSuite grids when wheel does not.
        try:
            ActionChains(driver).send_keys(Keys.PAGE_DOWN).perform()
            time.sleep(0.45)
            changed = wait_for_visible_indexes_change(driver, before_indexes, timeout=4)
        except Exception:
            pass

    after_indexes = visible_grid_row_indexes(driver)

    return {
        "changed": changed or after_indexes != before_indexes,
        "before_indexes": before_indexes,
        "after_indexes": after_indexes,
    }


def wait_for_grid_rows_to_change(driver, old_signature, timeout=6):
    """
    After scrolling, wait for NetSuite to recycle/render the next set of rows.
    """
    end = time.monotonic() + timeout

    while time.monotonic() < end:
        new_signature = grid_signature(driver)

        if new_signature and new_signature != old_signature:
            time.sleep(0.25)
            return True

        time.sleep(0.15)

    return False


def wait_for_grid_stable(driver, stable_for=0.8, timeout=12):
    """
    Wait until the grid stops changing for a short period.
    This is better than a blind time.sleep.
    """
    start = time.monotonic()
    last_sig = ""
    stable_start = None

    while time.monotonic() - start < timeout:
        sig = grid_signature(driver)

        if sig and sig == last_sig:
            if stable_start is None:
                stable_start = time.monotonic()

            if time.monotonic() - stable_start >= stable_for:
                return True
        else:
            stable_start = None
            last_sig = sig

        time.sleep(0.2)

    return False


def click_query_api_child(driver, child_item, record_name, record_id, timeout=None):
    """
    Clicks the SuiteScript and REST Query API child and forces the right-side
    detail panel back to Fields.

    Important:
    After scraping Joins for one record, NetSuite keeps the right panel on the
    Joins tab. If we click the next record while Joins is still selected, the
    Fields grid may exist but remain hidden, so the scraper can falsely think
    the record has no fields. This function explicitly selects the right-side
    Fields tab after every child click.
    """
    set_active_grid(FIELDS_GRID)
    old_signature = grid_signature(driver)

    if timeout is not None:
        verify_timeouts = [timeout]
    else:
        verify_timeouts = NO_FIELDS_GRID_VERIFY_TIMEOUTS[:NO_FIELDS_GRID_VERIFY_ATTEMPTS]

    for verify_attempt, verify_timeout in enumerate(verify_timeouts, start=1):
        try:
            set_active_grid(FIELDS_GRID)

            content = child_item.find_element(By.CSS_SELECTOR, '[data-tree-section="content"]')

            # Reselect the child item. This loads/reloads the right-side detail panel.
            safe_click(driver, content)
            time.sleep(0.35)

            # Force the right-side detail panel to Fields before waiting for
            # SSAnalyticAPIFieldsDataGrid. This avoids reading/waiting on Joins.
            select_catalog_tab(driver, "Fields", timeout=min(15, verify_timeout))
            set_active_grid(FIELDS_GRID)

            logger.info(
                f"🔎 Waiting for fields grid for {record_name} | {record_id} "
                f"| no-fields verification {verify_attempt}/{len(verify_timeouts)} "
                f"| timeout={verify_timeout}s"
            )

            grid = wait_for_grid_to_match_record(
                driver,
                record_name,
                record_id,
                old_signature=old_signature,
                timeout=verify_timeout,
            )

            wait_for_grid_stable(driver, stable_for=0.8, timeout=12)
            return grid

        except TimeoutException:
            if verify_attempt < len(verify_timeouts):
                logger.warning(
                    f"⚠️ No visible Fields grid yet for {record_name} | {record_id}; "
                    f"rechecking after {NO_FIELDS_GRID_RECHECK_PAUSE}s."
                )
                time.sleep(NO_FIELDS_GRID_RECHECK_PAUSE)
                continue

    logger.info(
        f"ℹ️ No visible Fields grid/table appeared for {record_name} | {record_id} "
        f"after {len(verify_timeouts)} verification attempt(s). "
        "Treating as verified 0 fields."
    )
    return None


# def get_grid_viewport(driver):
#     try:
#         return driver.find_element(By.CSS_SELECTOR, GRID_VIEWPORT)
#     except NoSuchElementException:
#         return driver.find_element(By.CSS_SELECTOR, GRID)


# def reset_grid_scroll(driver):
#     viewport = get_grid_viewport(driver)
#     driver.execute_script(
#         """
#         const el = arguments[0];
#         el.scrollTop = 0;
#         el.dispatchEvent(new Event('scroll', { bubbles: true }));
#         """,
#         viewport,
#     )
#     time.sleep(0.25)


# def scroll_grid_down(driver):
#     viewport = get_grid_viewport(driver)
#     return driver.execute_script(
#         """
#         const el = arguments[0];
#         const before = el.scrollTop;
#         const step = Math.max(200, el.clientHeight * 0.85);

#         el.scrollTop = Math.min(el.scrollTop + step, el.scrollHeight);
#         el.dispatchEvent(new Event('scroll', { bubbles: true }));

#         return {
#             before: before,
#             after: el.scrollTop,
#             clientHeight: el.clientHeight,
#             scrollHeight: el.scrollHeight,
#             atBottom: (el.scrollTop + el.clientHeight + 5) >= el.scrollHeight
#         };
#         """,
#         viewport,
#     )


def clean_text(value):
    return " ".join((value or "").split()).strip()


def extract_cell_text(cell, column_index):
    """
    The grid uses text spans, labels, SVG icons, and sometimes buttons.
    For Available, the check icon appears as an SVG with aria-label='Available'.
    """
    bits = []

    # Text spans and labels
    for node in cell.find_elements(By.CSS_SELECTOR, 'span[data-widget="Text"], label'):
        text = clean_text(node.text)
        if text:
            bits.append(text)

    # Link-style buttons, especially in Join column
    for btn in cell.find_elements(By.CSS_SELECTOR, "button[aria-label]"):
        label = clean_text(btn.get_attribute("aria-label"))
        if label and label not in {"Copy To Clipboard", "Info"}:
            bits.append(label)

    # Available checkmark column
    if column_index == 3:
        available_icon = cell.find_elements(By.CSS_SELECTOR, 'svg[aria-label="Available"]')
        if available_icon:
            return "Yes"

    # Remove duplicate text while preserving order
    seen = set()
    unique_bits = []
    for bit in bits:
        if bit not in seen:
            seen.add(bit)
            unique_bits.append(bit)

    return " ".join(unique_bits)


def get_direct_grid_cells(row):
    """
    Returns only the direct cell elements for a grid row.

    NetSuite grids contain many nested divs/buttons/spans, so using direct
    children avoids accidentally reading nested content as separate cells.
    """
    try:
        cells = row.find_elements(By.CSS_SELECTOR, ':scope > div[data-widget][data-index]')
    except Exception:
        cells = row.find_elements(
            By.CSS_SELECTOR,
            (
                'div[data-widget="TreeCell"][data-index], '
                'div[data-widget="TemplatedCell"][data-index], '
                'div[data-widget="TextBoxCell"][data-index]'
            ),
        )

    def cell_index(cell):
        try:
            return int(cell.get_attribute("data-index") or 0)
        except ValueError:
            return 0

    return sorted(cells, key=cell_index)


def parse_synthetic_row(row):
    """
    Extracts nested record metadata from synthetic rows.

    For example, expanding Sales Order.billingAddress creates a synthetic row
    containing salesOrderBillingAddress + Address before the child address fields.
    """
    texts = [
        clean_text(node.text)
        for node in row.find_elements(By.CSS_SELECTOR, 'span[data-widget="Text"]')
        if clean_text(node.text)
    ]

    nested_record_id = texts[0] if len(texts) >= 1 else ""
    nested_record_name = texts[1] if len(texts) >= 2 else ""

    return nested_record_id, nested_record_name


def parse_grid_row(record_name, record_id, row, parent_lookup=None, nested_lookup=None):
    parent_lookup = parent_lookup if parent_lookup is not None else {}
    nested_lookup = nested_lookup if nested_lookup is not None else {}

    cells = get_direct_grid_cells(row)
    values = [""] * 7

    for cell in cells:
        try:
            column_index = int(cell.get_attribute("data-index") or 0)
        except ValueError:
            continue

        if 0 <= column_index <= 6:
            values[column_index] = extract_cell_text(cell, column_index)

    field_id = values[0]

    if not field_id:
        return None

    row_id = row.get_attribute("data-row-id") or ""
    parent_row_id = row.get_attribute("data-parent-row-id") or ""

    parent_info = parent_lookup.get(parent_row_id, {}) if parent_row_id else {}
    parent_field_id = parent_info.get("Field ID", "")
    parent_field_path = parent_info.get("Field Path", parent_field_id)

    nested_info = nested_lookup.get(parent_row_id, {}) if parent_row_id else {}
    nested_record_id = nested_info.get("Nested Record ID", "")
    nested_record_name = nested_info.get("Nested Record Name", "")

    is_subfield = "Yes" if parent_row_id else "No"
    field_path = f"{parent_field_path}.{field_id}" if parent_field_path else field_id

    item = {
        "_row_index": row.get_attribute("data-index") or "",
        "_row_id": row_id,
        "_parent_row_id": parent_row_id,
        "Record Name": record_name,
        "Record ID": record_id,
        "Field ID": values[0],
        "Name": values[1],
        "Type": values[2],
        "Available": values[3],
        "Feature": values[4],
        "Permission": values[5],
        "Join": values[6],
        "Is Subfield": is_subfield,
        "Parent Field ID": parent_field_id,
        "Field Path": field_path,
        "Nested Record ID": nested_record_id,
        "Nested Record Name": nested_record_name,
    }

    if row_id:
        parent_lookup[row_id] = {
            "Field ID": field_id,
            "Field Path": field_path,
        }

    return item


def expand_visible_field_rows(driver, max_passes=3):
    """
    Expands any visible expandable field rows so their child/subfield rows become
    part of the virtual grid stream and can be scraped during normal scrolling.
    """
    set_active_grid(FIELDS_GRID)
    total_clicked = 0

    for _ in range(max_passes):
        clicked_this_pass = 0

        try:
            grid = get_grid(driver)
            rows = grid.find_elements(
                By.CSS_SELECTOR,
                '[data-widget="GridRowSegment"][data-row-type="data"]',
            )
        except (NoSuchElementException, StaleElementReferenceException):
            break

        for row in rows:
            try:
                expanders = row.find_elements(
                    By.CSS_SELECTOR,
                    '[data-widget="Tree"][data-expandable="true"] '
                    '[data-tree-section="expander"][aria-expanded="false"]',
                )

                if not expanders:
                    continue

                safe_click(driver, expanders[0])
                clicked_this_pass += 1
                total_clicked += 1
                time.sleep(0.2)

            except (StaleElementReferenceException, WebDriverException):
                continue

        if clicked_this_pass == 0:
            break

        wait_for_grid_stable(driver, stable_for=0.4, timeout=6)

    if total_clicked:
        logger.info(f"🧩 Expanded {total_clicked} visible nested field row(s).")

    return total_clicked


def read_visible_grid_rows(driver, record_name, record_id, parent_lookup=None, nested_lookup=None):
    set_active_grid(FIELDS_GRID)

    parent_lookup = parent_lookup if parent_lookup is not None else {}
    nested_lookup = nested_lookup if nested_lookup is not None else {}

    grid = get_grid(driver)

    all_rows = grid.find_elements(By.CSS_SELECTOR, '[data-widget="GridRowSegment"]')

    # Synthetic rows carry nested-record headers for expanded child structures.
    for row in all_rows:
        try:
            if (row.get_attribute("data-row-type") or "") != "synthetic":
                continue

            parent_row_id = row.get_attribute("data-parent-row-id") or ""
            if not parent_row_id:
                continue

            nested_record_id, nested_record_name = parse_synthetic_row(row)
            nested_lookup[parent_row_id] = {
                "Nested Record ID": nested_record_id,
                "Nested Record Name": nested_record_name,
            }
        except StaleElementReferenceException:
            continue

    data_rows = [
        row for row in all_rows
        if (row.get_attribute("data-row-type") or "") == "data"
    ]

    data_rows.sort(
        key=lambda r: int(r.get_attribute("data-index") or 0)
        if (r.get_attribute("data-index") or "0").isdigit()
        else 0
    )

    parsed = []

    for row in data_rows:
        try:
            item = parse_grid_row(
                record_name,
                record_id,
                row,
                parent_lookup=parent_lookup,
                nested_lookup=nested_lookup,
            )
            if item:
                parsed.append(item)
        except StaleElementReferenceException:
            continue

    return parsed


def scrape_fields_grid(driver, record_name, record_id, max_scrolls=180):
    """
    Scrapes all rows from the Fields tab, including nested/subfield rows exposed
    by expandable field rows like Sales Order.billingAddress.
    """
    set_active_grid(FIELDS_GRID)
    select_catalog_tab(driver, "Fields")

    get_grid(driver)
    wait_for_grid_stable(driver, stable_for=0.8, timeout=12)

    reset_fields_grid_to_top(driver)
    wait_for_grid_stable(driver, stable_for=0.6, timeout=8)

    collected = {}
    parent_lookup = {}
    nested_lookup = {}

    for scroll_round in range(1, max_scrolls + 1):
        indexes_before_scrape = visible_grid_row_indexes(driver)

        logger.info(
            f"🧭 Fields {record_name} | round={scroll_round} | "
            f"visible indexes before scrape={indexes_before_scrape} | "
            f"collected={len(collected)}"
        )

        expand_visible_field_rows(driver)

        rows = read_visible_grid_rows(
            driver,
            record_name,
            record_id,
            parent_lookup=parent_lookup,
            nested_lookup=nested_lookup,
        )

        for item in rows:
            field_path = item.get("Field Path", "") or item.get("Field ID", "")
            row_index = item.get("_row_index", "")

            if not field_path:
                continue

            key = f"{row_index}:{field_path}" if row_index else field_path
            collected[key] = item

        collected_after_scrape = len(collected)
        scroll_result = scroll_fields_grid_down(driver)

        logger.info(
            f"🧭 Fields {record_name} | round={scroll_round} | "
            f"after scroll indexes={scroll_result['after_indexes']} | "
            f"changed={scroll_result['changed']} | "
            f"collected={collected_after_scrape}"
        )

        if not scroll_result["changed"]:
            # One final expansion/read after the last scroll attempt catches
            # rows inserted at the bottom by a newly expanded parent.
            expand_visible_field_rows(driver)
            for item in read_visible_grid_rows(
                driver,
                record_name,
                record_id,
                parent_lookup=parent_lookup,
                nested_lookup=nested_lookup,
            ):
                field_path = item.get("Field Path", "") or item.get("Field ID", "")
                row_index = item.get("_row_index", "")
                if field_path:
                    key = f"{row_index}:{field_path}" if row_index else field_path
                    collected[key] = item

            logger.info(
                f"🛑 Stopping Fields scrape for {record_name}: "
                "no further row-index movement after scroll."
            )
            break

        wait_for_grid_stable(driver, stable_for=0.4, timeout=6)

    cleaned = []

    for item in collected.values():
        item.pop("_row_index", None)
        item.pop("_row_id", None)
        item.pop("_parent_row_id", None)
        cleaned.append(item)

    cleaned.sort(
        key=lambda row: (
            row.get("Record Name", ""),
            row.get("Field Path", ""),
            row.get("Field ID", ""),
        )
    )

    return cleaned


def first_button_label(cell):
    for btn in cell.find_elements(By.CSS_SELECTOR, "button[aria-label]"):
        label = clean_text(btn.get_attribute("aria-label"))
        if label and label not in {"Copy To Clipboard", "Info"}:
            return label
    return ""


def weak_text(cell):
    nodes = cell.find_elements(
        By.CSS_SELECTOR,
        'span[data-type="weak"], span[data-color="secondary"]',
    )
    for node in nodes:
        value = clean_text(node.text)
        if value:
            return value
    return ""


def info_label(cell):
    labels = []

    for node in cell.find_elements(By.CSS_SELECTOR, 'svg[aria-label]'):
        value = clean_text(node.get_attribute("aria-label"))
        if value and value not in {"", "Info"}:
            labels.append(value)

    # Prefer the relationship/condition label when present.
    for value in labels:
        if "=" in value:
            return value

    return labels[0] if labels else ""


def checkmark_present(cell):
    return bool(
        cell.find_elements(By.CSS_SELECTOR, 'svg[data-icon*="CHECK"]')
        or cell.find_elements(By.CSS_SELECTOR, 'svg[aria-label="Available"]')
    )


def parse_join_category_row(row):
    """
    Extracts the Joins tab category/sublists header.

    In NetSuite this appears as a synthetic row above a group of join rows, for
    example: Address / salesOrderShippingAddress.
    """
    texts = [
        clean_text(node.text)
        for node in row.find_elements(By.CSS_SELECTOR, 'span[data-widget="Text"]')
        if clean_text(node.text)
    ]

    category_name = texts[0] if len(texts) >= 1 else ""
    category_id = texts[1] if len(texts) >= 2 else ""

    return category_name, category_id


def parse_join_row(record_name, record_id, row, category_state=None, parent_lookup=None):
    category_state = category_state if category_state is not None else {}
    parent_lookup = parent_lookup if parent_lookup is not None else {}

    cells = get_direct_grid_cells(row)
    values = [""] * 5

    for cell in cells:
        try:
            column_index = int(cell.get_attribute("data-index") or 0)
        except ValueError:
            continue

        if 0 <= column_index <= 4:
            values[column_index] = extract_cell_text(cell, column_index)

    cell_by_index = {}
    for cell in cells:
        try:
            cell_by_index[int(cell.get_attribute("data-index") or 0)] = cell
        except ValueError:
            pass

    cell0 = cell_by_index.get(0)
    cell1 = cell_by_index.get(1)
    cell2 = cell_by_index.get(2)
    cell3 = cell_by_index.get(3)
    cell4 = cell_by_index.get(4)

    join_type = values[0]
    join_kind = info_label(cell0) if cell0 else ""

    target_name = first_button_label(cell1) if cell1 else ""
    target_record_id = weak_text(cell1) if cell1 else ""
    condition = info_label(cell1) if cell1 else ""

    source_field_id = values[2] if cell2 else ""
    cardinality = values[3] if cell3 else ""
    available = "Yes" if (cell4 and checkmark_present(cell4)) else ""

    if not any([join_type, target_name, target_record_id, source_field_id, condition]):
        return None

    row_id = row.get_attribute("data-row-id") or ""
    parent_row_id = row.get_attribute("data-parent-row-id") or ""

    parent_info = parent_lookup.get(parent_row_id, {}) if parent_row_id else {}
    parent_source_field_id = parent_info.get("Source Field ID", "")
    parent_join_path = parent_info.get("Join Path", parent_source_field_id)

    is_subjoin = "Yes" if parent_row_id else "No"
    join_path = (
        f"{parent_join_path}.{source_field_id}"
        if parent_join_path and source_field_id
        else source_field_id
    )

    item = {
        "_row_index": row.get_attribute("data-index") or "",
        "_row_id": row_id,
        "_parent_row_id": parent_row_id,
        "Record Name": record_name,
        "Record ID": record_id,
        "Category Name": category_state.get("Category Name", ""),
        "Category ID": category_state.get("Category ID", ""),
        "Join Type": join_type,
        "Join Kind": join_kind,
        "Target Name": target_name,
        "Target Record ID": target_record_id,
        "Source Field ID": source_field_id,
        "Cardinality": cardinality,
        "Available": available,
        "Condition": condition,
        "Is Subjoin": is_subjoin,
        "Parent Source Field ID": parent_source_field_id,
        "Join Path": join_path,
    }

    if row_id:
        parent_lookup[row_id] = {
            "Source Field ID": source_field_id,
            "Join Path": join_path,
        }

    return item


def expand_visible_join_rows(driver, max_passes=3):
    """
    Expands visible expandable rows in the Joins grid.

    Some joins expose sub-rows using the same virtual tree mechanics as Fields.
    We expand what is visible, then the normal scroll loop collects the inserted
    child rows.
    """
    set_active_grid(JOINS_GRID)
    total_clicked = 0

    for _ in range(max_passes):
        clicked_this_pass = 0

        try:
            grid = get_grid(driver)
            rows = grid.find_elements(
                By.CSS_SELECTOR,
                '[data-widget="GridRowSegment"][data-row-type="data"]',
            )
        except (NoSuchElementException, StaleElementReferenceException):
            break

        for row in rows:
            try:
                expanders = row.find_elements(
                    By.CSS_SELECTOR,
                    '[data-widget="Tree"][data-expandable="true"] '
                    '[data-tree-section="expander"][aria-expanded="false"]',
                )

                if not expanders:
                    continue

                safe_click(driver, expanders[0])
                clicked_this_pass += 1
                total_clicked += 1
                time.sleep(0.2)

            except (StaleElementReferenceException, WebDriverException):
                continue

        if clicked_this_pass == 0:
            break

        wait_for_grid_stable(driver, stable_for=0.4, timeout=6)

    if total_clicked:
        logger.info(f"🧩 Expanded {total_clicked} visible nested join row(s).")

    return total_clicked


def read_visible_join_rows(driver, record_name, record_id, category_state=None, parent_lookup=None):
    set_active_grid(JOINS_GRID)

    category_state = category_state if category_state is not None else {}
    parent_lookup = parent_lookup if parent_lookup is not None else {}

    grid = get_grid(driver)
    rows = grid.find_elements(By.CSS_SELECTOR, '[data-widget="GridRowSegment"]')

    def row_index(row):
        raw = row.get_attribute("data-index") or "0"
        return int(raw) if raw.isdigit() else 0

    rows = sorted(rows, key=row_index)
    parsed = []

    for row in rows:
        try:
            row_type = row.get_attribute("data-row-type") or ""

            if row_type == "synthetic":
                category_name, category_id = parse_join_category_row(row)
                if category_name or category_id:
                    category_state["Category Name"] = category_name
                    category_state["Category ID"] = category_id
                continue

            if row_type != "data":
                continue

            item = parse_join_row(
                record_name,
                record_id,
                row,
                category_state=category_state,
                parent_lookup=parent_lookup,
            )
            if item:
                parsed.append(item)

        except StaleElementReferenceException:
            continue

    return parsed


def scrape_joins_grid(driver, record_name, record_id, max_scrolls=220):
    """
    Scrapes the Joins tab into a separate CSV output.
    Includes category/sublists synthetic rows and expandable nested join rows.
    """
    set_active_grid(JOINS_GRID)

    try:
        select_catalog_tab(driver, "Joins")
        get_grid(driver)
    except TimeoutException:
        logger.info(f"ℹ️ No Joins grid appeared for {record_name}. Treating joins as 0.")
        return []

    wait_for_grid_stable(driver, stable_for=0.8, timeout=12)
    reset_fields_grid_to_top(driver)
    wait_for_grid_stable(driver, stable_for=0.6, timeout=8)

    collected = {}
    category_state = {"Category Name": "", "Category ID": ""}
    parent_lookup = {}

    for scroll_round in range(1, max_scrolls + 1):
        indexes_before_scrape = visible_grid_row_indexes(driver)

        logger.info(
            f"🧭 Joins {record_name} | round={scroll_round} | "
            f"visible indexes before scrape={indexes_before_scrape} | "
            f"collected={len(collected)}"
        )

        expand_visible_join_rows(driver)

        for item in read_visible_join_rows(
            driver,
            record_name,
            record_id,
            category_state=category_state,
            parent_lookup=parent_lookup,
        ):
            key_parts = [
                item.get("_row_index", ""),
                item.get("Category ID", ""),
                item.get("Join Type", ""),
                item.get("Target Record ID", ""),
                item.get("Source Field ID", ""),
                item.get("Condition", ""),
                item.get("Join Path", ""),
            ]
            key = "|".join(key_parts)
            collected[key] = item

        collected_after_scrape = len(collected)
        scroll_result = scroll_fields_grid_down(driver)

        logger.info(
            f"🧭 Joins {record_name} | round={scroll_round} | "
            f"after scroll indexes={scroll_result['after_indexes']} | "
            f"changed={scroll_result['changed']} | "
            f"collected={collected_after_scrape}"
        )

        if not scroll_result["changed"]:
            expand_visible_join_rows(driver)
            for item in read_visible_join_rows(
                driver,
                record_name,
                record_id,
                category_state=category_state,
                parent_lookup=parent_lookup,
            ):
                key_parts = [
                    item.get("_row_index", ""),
                    item.get("Category ID", ""),
                    item.get("Join Type", ""),
                    item.get("Target Record ID", ""),
                    item.get("Source Field ID", ""),
                    item.get("Condition", ""),
                    item.get("Join Path", ""),
                ]
                key = "|".join(key_parts)
                collected[key] = item

            logger.info(
                f"🛑 Stopping Joins scrape for {record_name}: "
                "no further row-index movement after scroll."
            )
            break

        wait_for_grid_stable(driver, stable_for=0.4, timeout=6)

    cleaned = []

    for item in collected.values():
        item.pop("_row_index", None)
        item.pop("_row_id", None)
        item.pop("_parent_row_id", None)
        cleaned.append(item)

    cleaned.sort(
        key=lambda row: (
            row.get("Record Name", ""),
            row.get("Category ID", ""),
            row.get("Target Record ID", ""),
            row.get("Join Path", ""),
            row.get("Condition", ""),
        )
    )

    return cleaned

def atomic_write_csv(filename, fieldnames, rows, max_replace_attempts=5):
    """
    Writes a CSV safely.

    On Windows, os.replace() can fail with WinError 5 if the target CSV is open
    in Excel, previewed by another program, or temporarily locked by OneDrive/
    antivirus. To avoid crashing the scrape, we retry, then write a timestamped
    fallback file.
    """
    base, ext = os.path.splitext(filename)
    if not ext:
        ext = ".csv"

    tmp_filename = f"{base}.{os.getpid()}.{int(time.time())}.tmp"

    with open(tmp_filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    for attempt in range(1, max_replace_attempts + 1):
        try:
            os.replace(tmp_filename, filename)
            return filename
        except PermissionError as e:
            logger.warning(
                f"⚠️ Could not replace {filename} because it is locked "
                f"(attempt {attempt}/{max_replace_attempts}): {e}"
            )
            time.sleep(1)

    fallback_filename = f"{base}.fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    os.replace(tmp_filename, fallback_filename)
    logger.warning(
        f"⚠️ {filename} stayed locked. Saved checkpoint fallback to "
        f"{fallback_filename}. Close Excel/preview panes before the next run."
    )
    return fallback_filename

def save_status_rows(status_rows, filename=STATUS_FILE):
    atomic_write_csv(filename, STATUS_FIELDNAMES, status_rows)
    logger.info(f"💾 Saved Record Catalog status to {filename}")


def add_status(
    status_rows,
    index,
    record_name,
    record_id,
    status,
    field_count=0,
    join_count=0,
    attempts=1,
    error="",
):
    status_rows.append({
        "Record Index": index + 1,
        "Record Name": record_name or "",
        "Record ID": record_id or "",
        "Status": status,
        "Field Count": field_count,
        "Join Count": join_count,
        "Attempts": attempts,
        "Error": clean_text(str(error))[:800] if error else "",
        "Timestamp": datetime.now().isoformat(timespec="seconds"),
    })


def checkpoint_results(field_rows, join_rows, status_rows, processed_count, force=False):
    """
    Saves partial field rows, join rows, and status rows every CHECKPOINT_EVERY
    records, or immediately when force=True.
    """
    if not force and processed_count % CHECKPOINT_EVERY != 0:
        return

    save_fields(field_rows, PARTIAL_FIELDS_FILE)
    save_joins(join_rows, PARTIAL_JOINS_FILE)
    save_status_rows(status_rows, STATUS_FILE)
    logger.info(
        f"🧷 Checkpoint saved after {processed_count} processed records "
        f"({len(field_rows)} field rows, {len(join_rows)} join rows)."
    )


def read_csv_dicts(filename):
    if not os.path.exists(filename):
        return []

    with open(filename, "r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_existing_progress():
    """
    Loads prior V2 partial field/join rows and status rows so a resumed run does
    not start from scratch.
    """
    if not RESUME_FROM_CHECKPOINT:
        return [], [], [], set()

    status_rows = read_csv_dicts(STATUS_FILE)

    if not status_rows:
        logger.info("ℹ️ No previous V2 status checkpoint found. Starting from record 1.")
        return [], [], [], set()

    if not os.path.exists(PARTIAL_FIELDS_FILE):
        raise RuntimeError(
            f"{STATUS_FILE} exists, but {PARTIAL_FIELDS_FILE} was not found. "
            "Do not resume without the partial fields CSV."
        )

    if not os.path.exists(PARTIAL_JOINS_FILE):
        raise RuntimeError(
            f"{STATUS_FILE} exists, but {PARTIAL_JOINS_FILE} was not found. "
            "Do not resume without the partial joins CSV."
        )

    field_rows = read_csv_dicts(PARTIAL_FIELDS_FILE)
    join_rows = read_csv_dicts(PARTIAL_JOINS_FILE)

    latest_status_by_index = {}
    for row in status_rows:
        try:
            record_index = int(row.get("Record Index", "0"))
        except ValueError:
            continue

        latest_status_by_index[record_index] = row

    done_indexes = {
        record_index - 1
        for record_index, row in latest_status_by_index.items()
        if (row.get("Status") or "").strip() in DONE_STATUSES
    }

    logger.info(
        f"🔁 Resume mode active: loaded {len(field_rows)} previous field rows, "
        f"{len(join_rows)} previous join rows, {len(status_rows)} status rows, "
        f"and {len(done_indexes)} completed records."
    )

    failed_or_unfinished = [
        record_index
        for record_index, row in latest_status_by_index.items()
        if (row.get("Status") or "").strip() not in DONE_STATUSES
    ]

    if failed_or_unfinished:
        logger.info(
            "🔁 Failed/unfinished/unverified records will be retried: "
            + ", ".join(str(i) for i in sorted(failed_or_unfinished)[:20])
            + ("..." if len(failed_or_unfinished) > 20 else "")
        )

    return field_rows, join_rows, status_rows, done_indexes


def remove_existing_record_rows(rows, record_id):
    if not record_id:
        return

    rows[:] = [
        row for row in rows
        if (row.get("Record ID") or "") != record_id
    ]


def scrape_record_catalogs(driver):
    logger.info("🔎 Scraping Record Catalogs…")

    ensure_show_unavailable_items(driver)

    total_records = get_total_records(driver)
    logger.info(f"📚 Found approximately {total_records} records in catalog after enabling unavailable items.")

    limit = TEST_LIMIT if TEST_LIMIT is not None else total_records
    limit = min(limit, total_records)

    field_results, join_results, status_rows, done_indexes = load_existing_progress()
    processed_count = 0
    stop_scrape = False
    skipped_done = 0

    try:
        for index in range(limit):
            if index in done_indexes:
                skipped_done += 1
                if skipped_done % 100 == 0:
                    logger.info(f"⏭️ Skipped {skipped_done} already-completed records from V2 checkpoint.")
                continue

            logger.info(f"\n➡️ Processing record index {index + 1}/{limit}")

            parent_id = None
            record_name = ""
            record_id = ""
            record_done = False
            force_checkpoint_now = False

            for open_attempt in range(1, EXPAND_CLICK_MAX_ATTEMPTS + 1):
                try:
                    set_active_grid(FIELDS_GRID)
                    ensure_left_records_tab(driver, timeout=5)

                    record_item = scroll_tree_to_index(driver, index)
                    record_name, record_id = extract_record_identity(record_item)

                    if not record_name:
                        logger.warning(f"⚠️ Could not read record name at index {index}; skipping.")
                        force_checkpoint_now = True
                        add_status(
                            status_rows,
                            index,
                            record_name,
                            record_id,
                            "skipped_missing_name",
                            field_count=0,
                            join_count=0,
                            attempts=open_attempt,
                            error="Could not read record name",
                        )
                        record_done = True
                        break

                    logger.info(
                        f"📌 Record: {record_name} | {record_id} "
                        f"| open attempt {open_attempt}/{EXPAND_CLICK_MAX_ATTEMPTS}"
                    )

                    parent_id, child = expand_record(driver, record_item)
                    fields_grid = click_query_api_child(driver, child, record_name, record_id)

                    field_rows = []
                    join_rows = []
                    last_scrape_error = None

                    if fields_grid is None:
                        # Usually means there is no query API detail grid. We still
                        # try Joins once in case the Fields tab was slow/missing but
                        # the Joins tab exists.
                        try:
                            join_rows = scrape_joins_grid(driver, record_name, record_id)
                        except Exception:
                            join_rows = []

                        if not join_rows:
                            logger.info(
                                f"✅ Scraped 0 fields and 0 joins for {record_name} "
                                "(verified no catalog tables)."
                            )

                            add_status(
                                status_rows,
                                index,
                                record_name,
                                record_id,
                                "verified_no_catalog_tables",
                                field_count=0,
                                join_count=0,
                                attempts=open_attempt,
                            )
                            record_done = True
                            break

                    for scrape_attempt in range(1, GRID_SCRAPE_MAX_ATTEMPTS + 1):
                        try:
                            logger.info(
                                f"🧾 Scraping Fields + Joins for {record_name} "
                                f"| scrape attempt {scrape_attempt}/{GRID_SCRAPE_MAX_ATTEMPTS}"
                            )

                            if fields_grid is not None:
                                field_rows = scrape_fields_grid(driver, record_name, record_id)

                            join_rows = scrape_joins_grid(driver, record_name, record_id)

                            logger.info(
                                f"✅ Scraped {len(field_rows)} field rows and "
                                f"{len(join_rows)} join rows for {record_name}"
                            )

                            remove_existing_record_rows(field_results, record_id)
                            remove_existing_record_rows(join_results, record_id)

                            field_results.extend(field_rows)
                            join_results.extend(join_rows)

                            add_status(
                                status_rows,
                                index,
                                record_name,
                                record_id,
                                "success",
                                field_count=len(field_rows),
                                join_count=len(join_rows),
                                attempts=open_attempt,
                            )

                            record_done = True
                            break

                        except (TimeoutException, StaleElementReferenceException, WebDriverException) as e:
                            last_scrape_error = e
                            logger.warning(
                                f"⚠️ Field/join scrape failed for {record_name}, "
                                f"attempt {scrape_attempt}/{GRID_SCRAPE_MAX_ATTEMPTS}: {e}"
                            )
                            time.sleep(2)

                    if not record_done:
                        force_checkpoint_now = True
                        add_status(
                            status_rows,
                            index,
                            record_name,
                            record_id,
                            "failed_grid_scrape",
                            field_count=0,
                            join_count=0,
                            attempts=GRID_SCRAPE_MAX_ATTEMPTS,
                            error=last_scrape_error,
                        )
                        logger.warning(f"❌ Giving up field/join scrape for {record_name}.")
                        record_done = True

                    break

                except (TimeoutException, StaleElementReferenceException, ElementClickInterceptedException, ElementNotInteractableException) as e:
                    logger.warning(
                        f"⚠️ Open/expand/click failed on record index {index}, "
                        f"attempt {open_attempt}/{EXPAND_CLICK_MAX_ATTEMPTS}: {e}"
                    )

                    if parent_id:
                        collapse_record(driver, parent_id)
                        parent_id = None

                    if open_attempt < EXPAND_CLICK_MAX_ATTEMPTS:
                        time.sleep(2)
                        continue

                    force_checkpoint_now = True
                    add_status(
                        status_rows,
                        index,
                        record_name,
                        record_id,
                        "failed_open_expand_click",
                        field_count=0,
                        join_count=0,
                        attempts=open_attempt,
                        error=e,
                    )
                    record_done = True

                except WebDriverException as e:
                    logger.warning(f"🚨 WebDriver/browser issue on record index {index}: {e}")
                    force_checkpoint_now = True
                    add_status(
                        status_rows,
                        index,
                        record_name,
                        record_id,
                        "webdriver_error",
                        field_count=0,
                        join_count=0,
                        attempts=open_attempt,
                        error=e,
                    )
                    record_done = True
                    stop_scrape = True
                    break

                except Exception as e:
                    logger.warning(
                        f"⚠️ Unexpected error on record index {index}, "
                        f"attempt {open_attempt}/{EXPAND_CLICK_MAX_ATTEMPTS}: {e}"
                    )

                    if parent_id:
                        collapse_record(driver, parent_id)
                        parent_id = None

                    if open_attempt < EXPAND_CLICK_MAX_ATTEMPTS:
                        time.sleep(2)
                        continue

                    force_checkpoint_now = True
                    add_status(
                        status_rows,
                        index,
                        record_name,
                        record_id,
                        "failed_unexpected_error",
                        field_count=0,
                        join_count=0,
                        attempts=open_attempt,
                        error=e,
                    )
                    record_done = True

                finally:
                    if parent_id:
                        collapse_record(driver, parent_id)
                        parent_id = None

            if not record_done:
                force_checkpoint_now = True
                add_status(
                    status_rows,
                    index,
                    record_name,
                    record_id,
                    "failed_unknown",
                    field_count=0,
                    join_count=0,
                    attempts=EXPAND_CLICK_MAX_ATTEMPTS,
                    error="Record loop ended without completion flag",
                )

            processed_count += 1
            checkpoint_results(
                field_results,
                join_results,
                status_rows,
                processed_count,
                force=force_checkpoint_now,
            )

            if stop_scrape:
                logger.warning("🛑 Stopping mass scrape because the browser/WebDriver session became unstable.")
                break

    except KeyboardInterrupt:
        logger.warning("🛑 Scrape interrupted by user. Saving checkpoint before stopping…")
        checkpoint_results(field_results, join_results, status_rows, processed_count, force=True)
        return field_results, join_results

    checkpoint_results(field_results, join_results, status_rows, processed_count, force=True)

    logger.info(
        f"\n✅ Finished Record Catalog scrape. "
        f"Total field rows: {len(field_results)}. Total join rows: {len(join_results)}."
    )

    return field_results, join_results


def save_fields(rows, filename=FINAL_FIELDS_FILE):
    atomic_write_csv(filename, FIELDNAMES, rows)
    logger.info(f"💾 Saved Record Catalog fields to {filename}")


def save_joins(rows, filename=FINAL_JOINS_FILE):
    atomic_write_csv(filename, JOIN_FIELDNAMES, rows)
    logger.info(f"💾 Saved Record Catalog joins to {filename}")


def run(driver):
    switch_to_admin_role(driver)
    navigate_to_record_catalog(driver)

    field_rows, join_rows = scrape_record_catalogs(driver)

    save_fields(field_rows, FINAL_FIELDS_FILE)
    save_joins(join_rows, FINAL_JOINS_FILE)
