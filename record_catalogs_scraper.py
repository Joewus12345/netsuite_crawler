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


class IncompleteGridScrapeError(WebDriverException):
    """Raised when a grid keeps moving until the configured scroll safety cap."""


ADMIN_ROLE_URL = (
    "https://4891605.app.netsuite.com/app/login/secure/changerole.nl?"
    "id=4891605~10457~1073~N"
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

# A virtualized NetSuite grid can ignore one wheel/PageDown event while it is
# hydrating. Never declare the end of a Fields/Joins grid after one stalled
# scroll. The scraper retries the same boundary several times before stopping.
GRID_SCROLL_STALL_MAX_ATTEMPTS = 4
GRID_SCROLL_STALL_RETRY_PAUSE = 0.9
GRID_SCROLL_CHANGE_TIMEOUT = 3
GRID_SCROLL_FALLBACK_TIMEOUT = 2

# Resume repair policy:
#
# The old status file can say ``success`` even when a virtualized grid stopped
# moving early. A status count alone cannot prove that every field was reached.
# Therefore completed records that contain catalog data are queued for a
# ONE-TIME full Fields + Joins repair pass. The repaired status names below are
# final, so later resumes do not keep scraping the same records forever.
#
# Records already proven to have no Fields and no Joins remain untouched.
REPAIR_COMPLETED_CATALOG_RECORDS_ON_RESUME = True
REPAIR_SOURCE_STATUSES = {"success", "verified_zero_joins"}
REPAIR_FINAL_STATUSES = {
    "repair_success",
    "repair_verified_zero_joins",
    "repair_success_zero_fields",
}

# Optional test/scope controls. Use 1-based Record Index values, for example
# {999, 1000}. Leave as None to repair every eligible record from the status.
REPAIR_ONLY_RECORD_INDEXES = None
REPAIR_RECORD_LIMIT = None

# Resume policy:
# - Keep the V2 partial CSVs and V2 status CSV in the same folder.
# - On the next run, the scraper loads them, skips records already marked done,
#   and continues with failed/unseen records.
# - IMPORTANT: old no_fields_grid rows are NOT treated as done anymore because
#   they can be false positives caused by slow NetSuite loading/connectivity.
RESUME_FROM_CHECKPOINT = True

# Old `no_fields_grid` rows are not final; V2 uses a fresh status file.
# Only `verified_no_catalog_tables` is considered final/no-fields/no-joins.
DONE_STATUSES = {
    "success",
    "verified_zero_joins",
    "verified_no_catalog_tables",
    "skipped_missing_name",
    *REPAIR_FINAL_STATUSES,
}

# Resume/recheck policy for records that previously succeeded with 0 joins:
# A previous run may have marked Join Count = 0 because the Joins tab was slow
# or connectivity delayed hydration. On resume, these records are deliberately
# re-opened and Joins-only is scraped again. If Joins still returns 0 after the
# full Joins verification sequence, the record is marked verified_zero_joins so
# future resumes can safely skip it.
RECHECK_ZERO_JOINS_ON_RESUME = True
ZERO_JOINS_RECHECK_SOURCE_STATUSES = {"success"}

# Prevent the resume pass from spending minutes rechecking records that had
# neither Fields nor Joins in the previous run. Those are usually empty catalog
# records, not false-zero Joins.
RECHECK_ZERO_JOINS_REQUIRE_EXISTING_FIELDS = True

# If you want to recheck only the first N zero-join records during testing,
# set this to a number like 10. Leave as None for full resume behavior.
ZERO_JOINS_RECHECK_LIMIT = None

# If a record seems to have no fields, verify that conclusion with longer waits
# before writing `verified_no_catalog_tables`.
NO_FIELDS_GRID_VERIFY_ATTEMPTS = 3
NO_FIELDS_GRID_VERIFY_TIMEOUTS = [12, 25, 45]
NO_FIELDS_GRID_RECHECK_PAUSE = 2.5

# Apply the same verification idea to the Joins tab.
# Some records load Fields quickly but hydrate Joins much later. Previously,
# scrape_joins_grid() returned [] after one timeout, which could create a false
# "0 joins" result. These settings make Joins re-check 3 times before accepting
# that a record has no joins.
NO_JOINS_GRID_VERIFY_ATTEMPTS = 3
NO_JOINS_GRID_VERIFY_TIMEOUTS = [12, 25, 45]
NO_JOINS_GRID_RECHECK_PAUSE = 2.5

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


def active_grid_scroll_state(driver):
    """Return scroll position for the active grid's best vertical scroll box."""
    try:
        box = get_grid_scroll_box(driver, "y")
        return driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return {top: 0, maxTop: 0, atBottom: true};
            const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
            return {
                top: el.scrollTop,
                maxTop,
                atBottom: maxTop <= 5 || el.scrollTop >= maxTop - 3
            };
            """,
            box,
        )
    except Exception:
        return {"top": 0, "maxTop": 0, "atBottom": False}


def scroll_fields_grid_down(driver):
    """
    Scroll down inside the fields grid and verify whether the rendered row
    indexes changed.
    """
    focus_fields_grid(driver)

    before_indexes = visible_grid_row_indexes(driver)

    # First try native wheel. This is the most important part.
    native_wheel_grid(driver, 650, pause=0.45)

    changed = wait_for_visible_indexes_change(
        driver, before_indexes, timeout=GRID_SCROLL_CHANGE_TIMEOUT
    )

    scroll_state = active_grid_scroll_state(driver)

    if not changed and not scroll_state.get("atBottom", False):
        # Fallback: PageDown sometimes triggers NetSuite grids when wheel does not.
        try:
            ActionChains(driver).send_keys(Keys.PAGE_DOWN).perform()
            time.sleep(0.35)
            changed = wait_for_visible_indexes_change(
                driver,
                before_indexes,
                timeout=GRID_SCROLL_FALLBACK_TIMEOUT,
            )
        except Exception:
            pass

    after_indexes = visible_grid_row_indexes(driver)
    scroll_state = active_grid_scroll_state(driver)

    return {
        "changed": changed or after_indexes != before_indexes,
        "before_indexes": before_indexes,
        "after_indexes": after_indexes,
        "at_bottom": bool(scroll_state.get("atBottom", False)),
        "scroll_top": scroll_state.get("top", 0),
        "max_scroll_top": scroll_state.get("maxTop", 0),
    }


def advance_grid_with_stall_retries(driver, grid_label, record_name):
    """
    Attempts to advance the active virtual grid several times.

    NetSuite may consume a wheel event without recycling the visible rows while
    the grid is still hydrating. The previous implementation stopped scraping
    after the first unchanged row range, which could mark partial data as a
    successful scrape. This helper only reports the end after repeated stalls.
    """
    last_result = {
        "changed": False,
        "before_indexes": visible_grid_row_indexes(driver),
        "after_indexes": visible_grid_row_indexes(driver),
        "at_bottom": False,
        "scroll_top": 0,
        "max_scroll_top": 0,
    }

    for stall_attempt in range(1, GRID_SCROLL_STALL_MAX_ATTEMPTS + 1):
        last_result = scroll_fields_grid_down(driver)
        if last_result["changed"]:
            return last_result

        logger.info(
            f"⏳ {grid_label} {record_name}: scroll did not advance "
            f"({stall_attempt}/{GRID_SCROLL_STALL_MAX_ATTEMPTS}); "
            f"at_bottom={last_result.get('at_bottom', False)}."
        )

        # Two unchanged attempts while the actual scroll box is at the bottom
        # are enough to confirm the end. Away from the bottom, use all retries.
        if last_result.get("at_bottom", False) and stall_attempt >= 2:
            return last_result

        # Let delayed row virtualization finish, then refocus before the next
        # native wheel/PageDown attempt.
        wait_for_grid_stable(driver, stable_for=0.35, timeout=2)
        time.sleep(GRID_SCROLL_STALL_RETRY_PAUSE)

    return last_result


def field_item_key(item):
    """Stable de-duplication key that does not depend on shifting row indexes."""
    row_id = item.get("_row_id", "")
    if row_id:
        return f"row:{row_id}"

    return "|".join([
        item.get("Record ID", ""),
        item.get("Field Path", "") or item.get("Field ID", ""),
        item.get("Nested Record ID", ""),
        item.get("Parent Field ID", ""),
    ])


def join_item_key(item):
    """Stable Joins de-duplication key that survives virtual row recycling."""
    row_id = item.get("_row_id", "")
    if row_id:
        return f"row:{row_id}"

    return "|".join([
        item.get("Record ID", ""),
        item.get("Category ID", ""),
        item.get("Join Type", ""),
        item.get("Target Record ID", ""),
        item.get("Source Field ID", ""),
        item.get("Condition", ""),
        item.get("Join Path", ""),
    ])


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



def click_query_api_child_for_joins_only(driver, child_item):
    """
    Selects the SuiteScript and REST Query API child without waiting for Fields.

    This is used during the zero-join resume pass. Waiting for Fields here is
    unnecessary and can block progress on records whose Fields grid is empty or
    slow while we only want to verify Joins.
    """
    content = child_item.find_element(By.CSS_SELECTOR, '[data-tree-section="content"]')
    safe_click(driver, content)
    time.sleep(0.35)
    set_active_grid(JOINS_GRID)
    return True

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
            if not field_path:
                continue

            collected[field_item_key(item)] = item

        collected_after_scrape = len(collected)
        scroll_result = advance_grid_with_stall_retries(
            driver,
            "Fields",
            record_name,
        )

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
                if field_path:
                    collected[field_item_key(item)] = item

            logger.info(
                f"🛑 Stopping Fields scrape for {record_name}: "
                "no further row-index movement after scroll."
            )
            break

        wait_for_grid_stable(driver, stable_for=0.4, timeout=6)
    else:
        raise IncompleteGridScrapeError(
            f"Fields grid for {record_name} was still advancing after "
            f"{max_scrolls} scroll rounds; refusing to save a partial scrape."
        )

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



def visible_joins_content_state(driver):
    """
    Returns information about the visible Joins grid content.

    We only count body rows (data/synthetic), not the header row. This helps us
    distinguish between "the Joins tab is visible but still empty/loading" and
    "join rows are actually available to scrape".
    """
    set_active_grid(JOINS_GRID)

    try:
        grid = get_visible_grid_now(driver, JOINS_GRID)
        if not grid:
            return {
                "grid": None,
                "signature": "",
                "content_count": 0,
                "indexes": [],
            }

        rows = grid.find_elements(By.CSS_SELECTOR, '[data-widget="GridRowSegment"]')

        parts = []
        indexes = []
        content_count = 0

        for row in rows:
            row_type = row.get_attribute("data-row-type") or ""
            if row_type not in {"data", "synthetic"}:
                continue

            content_count += 1

            raw_index = row.get_attribute("data-index") or ""
            if raw_index.isdigit() and row_type == "data":
                indexes.append(int(raw_index))

            parts.append(
                "|".join([
                    row_type,
                    raw_index,
                    row.get_attribute("data-row-id") or "",
                    clean_text(row.text)[:120],
                ])
            )

        return {
            "grid": grid,
            "signature": "||".join(parts),
            "content_count": content_count,
            "indexes": sorted(set(indexes)),
        }

    except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
        return {
            "grid": None,
            "signature": "",
            "content_count": 0,
            "indexes": [],
        }


def wait_for_joins_grid_ready(driver, record_name, record_id, timeout=None):
    """
    Selects the right-side Joins tab and waits/retries until the Joins grid has
    actually hydrated.

    Returns:
    - visible Joins grid WebElement when content rows are available
    - None after 3 verification attempts when Joins appears to be genuinely empty

    This mirrors the Fields no-grid verification logic: Joins are not allowed to
    silently return [] after a single slow load/timeout.
    """
    set_active_grid(JOINS_GRID)

    if timeout is not None:
        verify_timeouts = [timeout]
    else:
        verify_timeouts = NO_JOINS_GRID_VERIFY_TIMEOUTS[:NO_JOINS_GRID_VERIFY_ATTEMPTS]

    for verify_attempt, verify_timeout in enumerate(verify_timeouts, start=1):
        try:
            set_active_grid(JOINS_GRID)

            logger.info(
                f"🔎 Waiting for Joins grid for {record_name} | {record_id} "
                f"| joins verification {verify_attempt}/{len(verify_timeouts)} "
                f"| timeout={verify_timeout}s"
            )

            select_catalog_tab(driver, "Joins", timeout=min(15, verify_timeout))
            get_grid(driver, JOINS_GRID, timeout=min(15, verify_timeout))

            end = time.monotonic() + verify_timeout
            last_signature = ""
            stable_empty_since = None

            while time.monotonic() < end:
                state = visible_joins_content_state(driver)

                if state["grid"] and state["content_count"] > 0:
                    wait_for_grid_stable(driver, stable_for=0.8, timeout=12)
                    return state["grid"]

                # If the visible Joins grid is empty, keep watching for a while.
                # NetSuite sometimes paints the tab/header first, then injects
                # the virtual rows later.
                current_signature = state["signature"]

                if state["grid"] and current_signature == last_signature:
                    if stable_empty_since is None:
                        stable_empty_since = time.monotonic()
                else:
                    stable_empty_since = None
                    last_signature = current_signature

                time.sleep(0.25)

            raise TimeoutException(
                f"Joins grid did not expose content rows within {verify_timeout}s"
            )

        except TimeoutException:
            if verify_attempt < len(verify_timeouts):
                logger.warning(
                    f"⚠️ No Joins rows/grid yet for {record_name} | {record_id}; "
                    f"rechecking after {NO_JOINS_GRID_RECHECK_PAUSE}s."
                )
                time.sleep(NO_JOINS_GRID_RECHECK_PAUSE)
                continue

    logger.info(
        f"ℹ️ No Joins grid rows appeared for {record_name} | {record_id} "
        f"after {len(verify_timeouts)} verification attempt(s). "
        "Treating joins as 0."
    )
    return None


def scrape_joins_grid(driver, record_name, record_id, max_scrolls=220):
    """
    Scrapes the Joins tab into a separate CSV output.
    Includes category/sublists synthetic rows and expandable nested join rows.

    The Joins tab now uses the same 3-step verification idea as Fields. It does
    not return [] after one slow timeout; it rechecks before accepting 0 joins.
    If the grid appears and then scraping fails midway, the outer
    GRID_SCRAPE_MAX_ATTEMPTS loop still retries the combined field/join scrape.
    """
    set_active_grid(JOINS_GRID)

    grid = wait_for_joins_grid_ready(driver, record_name, record_id)
    if grid is None:
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
            collected[join_item_key(item)] = item

        collected_after_scrape = len(collected)
        scroll_result = advance_grid_with_stall_retries(
            driver,
            "Joins",
            record_name,
        )

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
                collected[join_item_key(item)] = item

            logger.info(
                f"🛑 Stopping Joins scrape for {record_name}: "
                "no further row-index movement after scroll."
            )
            break

        wait_for_grid_stable(driver, stable_for=0.4, timeout=6)
    else:
        raise IncompleteGridScrapeError(
            f"Joins grid for {record_name} was still advancing after "
            f"{max_scrolls} scroll rounds; refusing to save a partial scrape."
        )

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


def safe_int(value, default=0):
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def should_recheck_zero_joins_status(row):
    """
    Returns True for records that completed successfully but had Join Count = 0.

    These are the records we want to reopen on resume because a zero join count
    can be a false positive when NetSuite's Joins tab hydrates slowly.
    """
    if not RECHECK_ZERO_JOINS_ON_RESUME:
        return False

    status = (row.get("Status") or "").strip()
    if status not in ZERO_JOINS_RECHECK_SOURCE_STATUSES:
        return False

    if safe_int(row.get("Join Count"), default=0) != 0:
        return False

    if RECHECK_ZERO_JOINS_REQUIRE_EXISTING_FIELDS:
        return safe_int(row.get("Field Count"), default=0) > 0

    return True


def record_identity_key(record_name, record_id):
    """Stable record key; falls back to the display name when ID is blank."""
    record_id = clean_text(record_id)
    if record_id:
        return f"id:{record_id}"

    record_name = clean_text(record_name)
    return f"name:{record_name}" if record_name else ""


def count_existing_record_rows(rows, record_id, record_name=""):
    wanted = record_identity_key(record_name, record_id)
    if not wanted:
        return 0

    return sum(
        1
        for row in rows
        if record_identity_key(row.get("Record Name"), row.get("Record ID")) == wanted
    )


def latest_status_rows_by_index(status_rows):
    """Return the last status row written for each 1-based Record Index."""
    latest = {}
    for row in status_rows:
        record_index = safe_int(row.get("Record Index"), default=0)
        if record_index > 0:
            latest[record_index] = row
    return latest


def choose_resume_mode(status_row, existing_field_count, existing_join_count):
    """
    Decide how a previously visited record should be handled on resume.

    Returns one of:
    - ``done``: keep existing output and skip the record
    - ``full``: re-scrape Fields and Joins, then atomically replace both
    - ``joins``: preserve Fields and verify/re-scrape Joins only

    A positive status count that disagrees with the partial CSV is always a
    full repair. A completed legacy status is also fully repaired once when
    REPAIR_COMPLETED_CATALOG_RECORDS_ON_RESUME is enabled, because legacy
    ``success`` rows cannot prove the old virtual-grid loop reached the bottom.
    """
    status = (status_row.get("Status") or "").strip()
    expected_fields = safe_int(status_row.get("Field Count"), default=0)
    expected_joins = safe_int(status_row.get("Join Count"), default=0)

    if status == "skipped_missing_name":
        return "done"

    output_count_mismatch = (
        existing_field_count != expected_fields
        or existing_join_count != expected_joins
    )

    # Preserve verified zero-field/zero-join records when their output is also
    # empty. If stale rows exist, repair once so status and CSV data agree.
    if status == "verified_no_catalog_tables":
        return "full" if output_count_mismatch else "done"

    # Even a final repaired status is retried if its checkpoint rows were lost
    # or duplicated after the status was written.
    if output_count_mismatch:
        return "full"

    if status in REPAIR_FINAL_STATUSES:
        return "done"

    if (
        REPAIR_COMPLETED_CATALOG_RECORDS_ON_RESUME
        and status in REPAIR_SOURCE_STATUSES
    ):
        # Include zero-field-with-joins and legacy verified_zero_joins records;
        # only verified_no_catalog_tables is exempt from repair.
        return "full"

    if should_recheck_zero_joins_status(status_row):
        return "joins"

    if status in DONE_STATUSES:
        return "done"

    # Failed, interrupted, unseen, and old unverified statuses are retried fully.
    return "full"


def load_existing_progress():
    """
    Load prior partial outputs/status and build a status-driven repair plan.

    The repair plan is keyed by zero-based tree index and contains ``full`` or
    ``joins``. Legacy completed records are repaired once and then receive a
    ``repair_*`` final status so future runs skip them. Records verified to have
    no catalog tables stay final and are never forced into the repair pass.
    """
    if not RESUME_FROM_CHECKPOINT:
        return [], [], [], set(), {}

    status_rows = read_csv_dicts(STATUS_FILE)

    if not status_rows:
        logger.info("ℹ️ No previous V2 status checkpoint found. Starting from record 1.")
        return [], [], [], set(), {}

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
    latest_status_by_index = latest_status_rows_by_index(status_rows)

    field_counts = {}
    for row in field_rows:
        key = record_identity_key(row.get("Record Name"), row.get("Record ID"))
        if key:
            field_counts[key] = field_counts.get(key, 0) + 1

    join_counts = {}
    for row in join_rows:
        key = record_identity_key(row.get("Record Name"), row.get("Record ID"))
        if key:
            join_counts[key] = join_counts.get(key, 0) + 1

    done_indexes = set()
    repair_modes = {}

    for record_index, row in latest_status_by_index.items():
        if REPAIR_ONLY_RECORD_INDEXES is not None and record_index not in REPAIR_ONLY_RECORD_INDEXES:
            # Explicit repair scoping only limits legacy completed repairs. It
            # does not hide failed records or corrupt output-count mismatches.
            status = (row.get("Status") or "").strip()
            key = record_identity_key(row.get("Record Name"), row.get("Record ID"))
            existing_fields = field_counts.get(key, 0)
            existing_joins = join_counts.get(key, 0)
            expected_fields = safe_int(row.get("Field Count"), default=0)
            expected_joins = safe_int(row.get("Join Count"), default=0)

            if (
                status in DONE_STATUSES
                and existing_fields == expected_fields
                and existing_joins == expected_joins
            ):
                done_indexes.add(record_index - 1)
                continue

        key = record_identity_key(row.get("Record Name"), row.get("Record ID"))
        mode = choose_resume_mode(
            row,
            field_counts.get(key, 0),
            join_counts.get(key, 0),
        )

        zero_based_index = record_index - 1
        if mode == "done":
            done_indexes.add(zero_based_index)
        else:
            repair_modes[zero_based_index] = mode

    if REPAIR_RECORD_LIMIT is not None:
        keep = set(sorted(repair_modes)[:REPAIR_RECORD_LIMIT])
        for index in list(repair_modes):
            if index not in keep:
                done_indexes.add(index)
                del repair_modes[index]

    mode_counts = {}
    for mode in repair_modes.values():
        mode_counts[mode] = mode_counts.get(mode, 0) + 1

    logger.info(
        f"🔁 Resume mode active: loaded {len(field_rows)} field rows, "
        f"{len(join_rows)} join rows, and {len(status_rows)} status rows. "
        f"Plan: {len(done_indexes)} done, "
        f"{mode_counts.get('full', 0)} full Fields+Joins repairs, "
        f"{mode_counts.get('joins', 0)} Joins-only repairs."
    )

    if repair_modes:
        preview = [
            f"{index + 1}:{repair_modes[index]}"
            for index in sorted(repair_modes)[:25]
        ]
        logger.info(
            "🔧 Status-driven repair queue (RecordIndex:mode): "
            + ", ".join(preview)
            + ("..." if len(repair_modes) > 25 else "")
        )

    return field_rows, join_rows, status_rows, done_indexes, repair_modes

def remove_existing_record_rows(rows, record_id, record_name=""):
    wanted = record_identity_key(record_name, record_id)
    if not wanted:
        return

    rows[:] = [
        row
        for row in rows
        if record_identity_key(row.get("Record Name"), row.get("Record ID")) != wanted
    ]


def scrape_record_catalogs(driver):
    logger.info("🔎 Scraping Record Catalogs…")

    ensure_show_unavailable_items(driver)

    total_records = get_total_records(driver)
    logger.info(f"📚 Found approximately {total_records} records in catalog after enabling unavailable items.")

    limit = TEST_LIMIT if TEST_LIMIT is not None else total_records
    limit = min(limit, total_records)

    field_results, join_results, status_rows, done_indexes, repair_modes = load_existing_progress()
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

            repair_mode = repair_modes.get(index, "full")
            recheck_joins_only = repair_mode == "joins"
            full_repair = index in repair_modes and repair_mode == "full"

            logger.info(f"\n➡️ Processing record index {index + 1}/{limit}")

            if recheck_joins_only:
                logger.info(
                    f"🔁 Record index {index + 1}: Joins-only repair; "
                    "preserving existing field rows."
                )
            elif full_repair:
                logger.info(
                    f"🔧 Record index {index + 1}: full Fields + Joins repair."
                )

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

                    if recheck_joins_only:
                        click_query_api_child_for_joins_only(driver, child)
                        fields_grid = None
                    else:
                        fields_grid = click_query_api_child(driver, child, record_name, record_id)

                    field_rows = []
                    join_rows = []
                    last_scrape_error = None

                    if recheck_joins_only:
                        existing_field_count = count_existing_record_rows(field_results, record_id, record_name)

                        for scrape_attempt in range(1, GRID_SCRAPE_MAX_ATTEMPTS + 1):
                            try:
                                logger.info(
                                    f"🔁 Rechecking Joins only for {record_name} "
                                    f"| attempt {scrape_attempt}/{GRID_SCRAPE_MAX_ATTEMPTS}"
                                )

                                join_rows = scrape_joins_grid(driver, record_name, record_id)

                                remove_existing_record_rows(join_results, record_id, record_name)
                                join_results.extend(join_rows)

                                status = (
                                    "repair_success"
                                    if join_rows
                                    else "repair_verified_zero_joins"
                                )

                                if join_rows:
                                    logger.info(
                                        f"✅ Rechecked Joins for {record_name}: "
                                        f"found {len(join_rows)} join row(s)."
                                    )
                                else:
                                    logger.info(
                                        f"✅ Rechecked Joins for {record_name}: "
                                        "still 0 joins after verification."
                                    )

                                add_status(
                                    status_rows,
                                    index,
                                    record_name,
                                    record_id,
                                    status,
                                    field_count=existing_field_count,
                                    join_count=len(join_rows),
                                    attempts=scrape_attempt,
                                )

                                force_checkpoint_now = True
                                record_done = True
                                break

                            except (TimeoutException, StaleElementReferenceException, WebDriverException) as e:
                                last_scrape_error = e
                                logger.warning(
                                    f"⚠️ Joins-only recheck failed for {record_name}, "
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
                                "failed_join_recheck",
                                field_count=existing_field_count,
                                join_count=0,
                                attempts=GRID_SCRAPE_MAX_ATTEMPTS,
                                error=last_scrape_error,
                            )
                            logger.warning(f"❌ Giving up Joins-only recheck for {record_name}.")
                            record_done = True

                        break

                    if fields_grid is None:
                        # Usually means there is no query API detail grid. We still
                        # try Joins once in case the Fields tab was slow/missing but
                        # the Joins tab exists.
                        join_rows = scrape_joins_grid(driver, record_name, record_id)

                        if not join_rows:
                            logger.info(
                                f"✅ Scraped 0 fields and 0 joins for {record_name} "
                                "(verified no catalog tables)."
                            )

                            remove_existing_record_rows(
                                field_results,
                                record_id,
                                record_name,
                            )
                            remove_existing_record_rows(
                                join_results,
                                record_id,
                                record_name,
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

                            remove_existing_record_rows(field_results, record_id, record_name)
                            remove_existing_record_rows(join_results, record_id, record_name)

                            field_results.extend(field_rows)
                            join_results.extend(join_rows)

                            if full_repair:
                                if field_rows and join_rows:
                                    completed_status = "repair_success"
                                elif field_rows and not join_rows:
                                    completed_status = "repair_verified_zero_joins"
                                elif not field_rows and join_rows:
                                    completed_status = "repair_success_zero_fields"
                                else:
                                    completed_status = "verified_no_catalog_tables"
                            else:
                                completed_status = "success"

                            add_status(
                                status_rows,
                                index,
                                record_name,
                                record_id,
                                completed_status,
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
