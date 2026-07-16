# 🚀 NetSuite Web Crawler

This project is a **Python-based web crawler** that **logs into NetSuite**, navigates to specific sections (like _Custom Records_), and extracts links for analysis. It supports **2FA authentication**, **security question handling**, and **headless mode for automation**.

---

## 📌 Features

✅ **Automated Login** (Email & Password)  
✅ **Handles 2FA** (Console Input in Headless Mode)  
✅ **Answers Security Questions Automatically**  
✅ **Crawls NetSuite Pages** & Extracts Links  
✅ **Supports Headless Mode for Automation**  
✅ **Navigates Directly to Custom Records**  
✅ **Scrapes User Role Permissions** across Transactions, Reports, Lists, Setup and Custom Record sections  
✅ **Extracts Workflow Actions** for any record type  
✅ **Scrapes Custom List Values into CSV**  
✅ **Exports Scraped Data to CSV**  
✅ **Handles Multi‐Page Role Lists with Pagination**

---

## ⚙️ Installation & Setup

### 1️⃣ **Clone the Repository**

```sh
git clone https://github.com/Joewus12345/netsuite_crawler.git
cd netsuite_crawler
```

---

### 2️⃣ **Set Up a Virtual Environment (Recommended)**

```sh
python -m venv venv
source venv/bin/activate  # On macOS/Linux
venv\Scripts\activate     # On Windows
```

---

### 3️⃣ **Install Dependencies**

```sh
pip install -r requirements.txt
```

---

### 4️⃣ **Set Up** config.py

Create a config.py file in the root directory and add your NetSuite credentials:

```sh
# config.py

# Login credentials
NETSUITE_URL = "https://your-netsuite-url.com"
NETSUITE_EMAIL = "your-email@example.com"
NETSUITE_PASSWORD = "your-password"

# Security Questions (Modify based on actual questions)
SECURITY_ANSWER = "your-security-question-answer"

# ✅ URL for Custom Record "Admin Item"
ADMIN_ITEM_URL = "https://4891605.app.netsuite.com/app/common/custom/custrecord.nl?id=<custom record id>" # Necessary for workflow scraping

# ✅ Configure WebDriver (Allow headless mode)
HEADLESS_MODE = False  # Change to True to run without opening a browser

import os

PERSIST_BROWSER_PROFILE = True

CHROME_PROFILE_DIR = os.path.abspath(
    os.path.join("selenium_profiles", "netsuite_chrome") #You can choose a different folder (selenium_profiles) and account profile (netsuite_chrome).
)

NETSUITE_BASE_URL = "https://4891605.app.netsuite.com" # Change the BASE_URL to match your NetSuite Base Url
```

> **Note:** The NetSuite administrator login URL can vary between companies or
> accounts. Replace `https://your-netsuite-url.com` with the correct admin URL
> for your environment.

---

## 🚀 **Running the Crawler**

Choose one or more scrapers to run using the `--scrapers` flag. Scrapers run
sequentially after a single login.

Available scrapers:

- `crawler`
- `workflows`
- `user-roles`
- `list-values`

### Default Output Files

Each scraper saves its results to a CSV file in the project root:

- `list-values` → **`list_values.csv`**, containing custom list IDs, names, and their associated values.
- `user-roles` → **`user_role_permissions.csv`**, capturing each role's permissions across transactions, reports, lists, and setup categories.
- `workflows` → **`workflow_actions.csv`**, listing workflow names, record types, and their associated actions.

### **Examples**

Scrape list values and user roles:

```sh
python main.py --scrapers list-values,user-roles
```

Scrape workflows for specific record types:

The `--records` flag expects a JSON array of record-type names. Quoting rules
vary by terminal:

**bash (Linux/macOS):**

```bash
python main.py --scrapers workflows --records '["Admin Request","Feedback"]'
```

**cmd.exe:**

```cmd
python main.py --scrapers workflows --records "[\"Admin Request\",\"Feedback\"]"
```

For quick runs you can pre-populate `workflow_scraper.HARDCODED` with a list of
record names. This list is used whenever `--records` is not passed:

```python
# workflow_scraper.py
HARDCODED: list[str] = ["Admin Request", "Feedback"]
```

### **Headless Mode (Without Browser)**

Edit config.py and set:

```sh
HEADLESS_MODE = True
```

Use the same command-line options; the browser runs hidden and prompts you in
the terminal for the 2FA code.

#### **NB: This only applies to user accounts with Administrative Privileges**

- Provide the 2FA code when prompted.

---

## 📂 Project Structure

```sh
📂 netsuite_crawler
 ┣ 📜 config.py              # Stores credentials & config
 ┣ 📂 chromedriver           # Chrome browser for running project
 ┣ 📜 main.py                # Entry point for the bot
 ┣ 📜 auth_utils.py          # Authentication helpers
 ┣ 📜 crawler.py             # Core logic for logging in & crawling
 ┣ 📜 list_values_scraper.py # Scrapes custom list values
 ┣ 📜 user_roles_scraper.py  # Scrapes role permissions
 ┣ 📜 workflow_scraper.py    # Scrapes workflow actions
 ┣ 📂 tests                  # Unit tests
 ┣ 📜 requirements.txt       # Dependencies list
 ┗ 📜 README.md              # Project documentation (You are here!)
```

---

## 🧪 Testing

Run the test suite with:

```bash
python -m pytest
```

The tests mock browser interactions and require no live NetSuite credentials.

---

## 🛠️ Troubleshooting

### 1️⃣ **WebDriver Issues?**

- Ensure Google Chrome is installed & updated.
- Download ChromeDriver from: [chromedriver.chromium.org](https://developer.chrome.com/docs/chromedriver/)

### 2️⃣ 2FA Not Submitting in Headless Mode?

- Check if the submit button selector is correct:

```sh
div[data-type='primary'][role='button']
```

### 3️⃣ Login Not Working?

- Try logging in manually to ensure credentials are correct.
- Check if NetSuite has CAPTCHA enabled (this bot does not bypass CAPTCHA).

### 4️⃣ Change Role Not Working?

- Check the role url which usually is different from user to user i.e. (
  ADMIN_ROLE_URL = (
  "<https://4891605.app.netsuite.com/app/login/secure/changerole.nl?id=4891605~18903~1114~N>"
  )
  )
  The section containing `changerole.nl?id=4891605~18903~1114~N` has a different unique number for each and every user (`18903` for user id and `1114` for role id need to be precise).
