# Development Guidelines

## Project structure
- `main.py` – command-line entry point. Builds a Chrome WebDriver and dispatches to the selected scrapers (`crawler`, `workflows`, `user-roles`, `list-values`).
- `crawler.py` – utilities for logging into NetSuite, navigating to the admin area, and crawling links.
- `workflow_scraper.py` – extracts workflow metadata. Includes helpers to switch roles, filter by record type, parse workflow definitions, and save results.
- `user_roles_scraper.py` – collects role permissions across all pages and writes them to CSV.
- `list_values_scraper.py` – gathers values for custom lists and writes them to CSV.
- `auth_utils.py` – shared helpers for switching roles and handling 2FA prompts.
- `tests/` – pytest suite. Browser interactions are mocked so no real NetSuite credentials are needed.

## Coding standards
- Follow [PEP 8](https://peps.python.org/pep-0008/) with 4‑space indentation.
- Type hints are encouraged for new or modified functions.

## Required checks
Run all tests and available linters before committing:
```bash
python -m pytest
flake8  # or another linter, if installed
```

## Running scrapers
Dispatch scrapers from `main.py`:
```bash
python main.py --scrapers workflows,user-roles
```
To provide record types for the workflow scraper, pass JSON via `--records`.
PowerShell example:
```powershell
python .\main.py --scrapers workflows --records '["Sales Order", "Purchase Order"]'
```

## Output
Scrapers write CSV files (`user_role_permissions.csv`, `list_values.csv`, etc.) to the current working directory.

## Testing notes
Browser interactions are always mocked in unit tests—no real NetSuite credentials are required.
