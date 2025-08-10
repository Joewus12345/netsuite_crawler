import argparse
import argparse
import json
import sys
import logging

from selenium import webdriver

import crawler
import workflow_scraper as ws
import user_roles_scraper as urs
import list_values_scraper as lvs

from config import HEADLESS_MODE


logging.basicConfig(level=logging.INFO, format="%(message)s")


def build_driver():
    """Configure and return a Chrome WebDriver respecting HEADLESS_MODE."""

    options = webdriver.ChromeOptions()
    if HEADLESS_MODE:
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    return driver


def parse_args():
    parser = argparse.ArgumentParser(description="NetSuite scraping dispatcher")
    parser.add_argument(
        "--scrapers",
        help=(
            "Comma-separated list of scrapers to run. "
            "Available: crawler, workflows, user-roles, list-values"
        ),
        default="",
    )
    parser.add_argument(
        "--records",
        help="JSON list of record-type names for the workflows scraper",
        default=None,
    )
    return parser.parse_args()


def main():
    args = parse_args()

    records = None
    if args.records:
        try:
            records = json.loads(args.records)
        except json.JSONDecodeError:
            print(
                "❌ Could not parse --records as JSON. Expecting a JSON array of strings."
            )
            sys.exit(1)

    driver = build_driver()

    scrapers = {
        "crawler": lambda d: crawler.run(d),
        "workflows": lambda d: ws.run(d, records),
        "user-roles": lambda d: urs.run(d),
        "list-values": lambda d: lvs.run(d),
    }

    crawler.login_netsuite(driver)

    requested = [s.strip() for s in args.scrapers.split(",") if s.strip()]
    valid = [s for s in requested if s in scrapers]

    if not valid:
        print("No valid scrapers specified. Available scrapers:")
        print(", ".join(scrapers.keys()))
        driver.quit()
        return

    for name in valid:
        scrapers[name](driver)

    driver.quit()


if __name__ == "__main__":
    main()

