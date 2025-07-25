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
@@ -45,88 +49,94 @@ pip install -r requirements.txt
---

### 4️⃣ **Set Up** config.py

Create a config.py file in the root directory and add your NetSuite credentials:

```sh
# config.py

NETSUITE_URL = "https://your-netsuite-url.com"
NETSUITE_EMAIL = "your-email@example.com"
NETSUITE_PASSWORD = "your-password"
SECURITY_ANSWER = "your-security-question-answer"
HEADLESS_MODE = False  # Change to True to run without opening a browser
```

---

## 🚀 **Running the Crawler**

### **Normal Mode (With Browser)**

```sh
python main.py
```
Running the script logs in and **scrapes permissions for all user roles**. The
results are saved to `user_role_permissions.csv` in the project directory.

#### **NB: This only applies to user accounts with Administrative Privileges**

- You manually enter the 2FA code in the browser when prompted.
- The bot automatically iterates through the role list and exports the CSV.

---

### **Headless Mode (Without Browser)**

Edit config.py and set:

```sh
HEADLESS_MODE = True
```

Then run:

```sh
python main.py
```
The process is the same as normal mode, but the browser window stays hidden. The
script prompts you for the 2FA code in the console and then scrapes all role
permissions automatically.

#### **NB: This only applies to user accounts with Administrative Privileges**

- Provide the 2FA code when prompted in the terminal.

---

## 📂 Project Structure

```sh
📂 netsuite_crawler
 ┣ 📂 chromedriver        # Stores driver for chrome browser
 ┣ 📂 venv                # Stores python environment
 ┣ 📜 config.py           # Stores credentials & config
 ┣ 📜 main.py             # Entry point for the bot
 ┣ 📜 crawler.py          # Core logic for logging in & crawling
 ┣ 📜 user_roles_scraper.py   # Scrapes role permissions
 ┣ 📜 workflow_scraper.py     # Scrapes workflow actions
 ┣ 📜 requirements.txt    # Dependencies list
 ┣ 📜 README.md           # Project documentation (You are here!)
```

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