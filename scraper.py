"""
Growatt OSS end-user table scraper.

After login the script clicks "End Users" in the left navigation menu,
then iterates every row across all pages.

First run  : opens a visible Chrome window, pauses for manual login,
             saves cookies, then scrapes all pages.
Later runs : loads saved cookies and runs headless automatically.

Usage:
    python scraper.py                  # scrape everything
    python scraper.py --test           # scrape only 1 row (first page only)
    python scraper.py --reset-session  # delete saved cookies and log in fresh
"""

import argparse
import csv
import json
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://oss.growatt.com"
LOGIN_URL = f"{BASE_URL}/login"
TABLE_URL = f"{BASE_URL}/index"

COOKIES_FILE  = "session_cookies.json"
OUTPUT_FILE   = "end_users.csv"

TABLE_ROW_SELECTOR = "tbody#tbl_data_plant tr"
NEXT_PAGE_SELECTOR = "a.layui-laypage-next"
END_USERS_MENU_SELECTOR = "#ul_menu_left_main li[data-url='deviceManage/userManage']"

# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------

def _chrome_options(headless: bool) -> webdriver.ChromeOptions:
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    return opts


def create_driver(headless: bool = False) -> webdriver.Chrome:
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=_chrome_options(headless),
    )


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def navigate_to_end_users(driver: webdriver.Chrome) -> None:
    """Click the End Users item in the left menu and wait for the table."""
    menu_item = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, END_USERS_MENU_SELECTOR))
    )
    menu_item.click()
    _wait_for_table(driver)
    print("[nav] End Users section loaded.")


# ---------------------------------------------------------------------------
# Cookie / session management
# ---------------------------------------------------------------------------

def save_cookies(driver: webdriver.Chrome) -> None:
    with open(COOKIES_FILE, "w") as fh:
        json.dump(driver.get_cookies(), fh, indent=2)
    print(f"[session] Cookies saved → {COOKIES_FILE}")


def load_cookies(driver: webdriver.Chrome) -> None:
    with open(COOKIES_FILE) as fh:
        for cookie in json.load(fh):
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass


def setup_session(reset: bool = False) -> webdriver.Chrome:
    """
    Returns a ready driver positioned on the End Users table.
    Handles first-run (headed + manual login) and subsequent runs (headless).
    """
    if reset and os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)
        print("[session] Existing cookies cleared.")

    # --- Try saved cookies first ---
    if os.path.exists(COOKIES_FILE):
        print("[session] Found saved cookies — trying headless mode...")
        driver = create_driver(headless=True)
        driver.get(BASE_URL)
        load_cookies(driver)
        driver.get(TABLE_URL)
        try:
            navigate_to_end_users(driver)
            print("[session] Session restored — running headless.")
            return driver
        except Exception:
            print("[session] Cookies expired. Falling back to manual login.")
            driver.quit()
            os.remove(COOKIES_FILE)

    # --- Manual login (headed) ---
    print("[session] Opening browser for manual login...")
    driver = create_driver(headless=False)
    driver.get(LOGIN_URL)

    print("\n" + "=" * 55)
    print("  Log in manually in the browser window that opened.")
    print("  Once you can see the main page, come back here")
    print("  and press ENTER to continue.")
    print("=" * 55 + "\n")
    input("  Press ENTER when ready > ")

    save_cookies(driver)

    if TABLE_URL not in driver.current_url:
        driver.get(TABLE_URL)

    navigate_to_end_users(driver)
    return driver


# ---------------------------------------------------------------------------
# Detail-page scraper  ← UPDATE THIS when you know the fields
# ---------------------------------------------------------------------------

def scrape_detail_page(driver: webdriver.Chrome) -> dict:
    """
    Called while the detail tab is active. Return a dict of the fields you need.

    TODO: Replace the placeholder lines below with real field extraction.
    Example patterns:
        driver.find_element(By.CSS_SELECTOR, ".some-class").text
        driver.find_element(By.XPATH, "//td[contains(text(),'Label')]/following-sibling::td").text
    """
    data = {}

    # --- PLACEHOLDER — replace with real selectors ---
    data["detail_page_title"] = driver.title
    data["detail_url"]        = driver.current_url
    # --- END PLACEHOLDER ---

    return data


# ---------------------------------------------------------------------------
# Row-by-row processing
# ---------------------------------------------------------------------------

def _wait_for_table(driver: webdriver.Chrome, timeout: int = 15) -> None:
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_ROW_SELECTOR))
    )
    time.sleep(0.8)


def process_row(driver: webdriver.Chrome, row_index: int) -> dict | None:
    main_handle = driver.current_window_handle

    rows = driver.find_elements(By.CSS_SELECTOR, TABLE_ROW_SELECTOR)
    if row_index >= len(rows):
        return None

    row = rows[row_index]

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", row)
    time.sleep(0.3)
    ActionChains(driver).double_click(row).perform()

    try:
        WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > 1)
    except Exception:
        print(f"    WARNING: Row {row_index + 1} — no new tab opened, skipping.")
        return None

    new_handle = next(h for h in driver.window_handles if h != main_handle)
    driver.switch_to.window(new_handle)

    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(1.5)
        data = scrape_detail_page(driver)
    except Exception as exc:
        print(f"    WARNING: Row {row_index + 1} — error scraping detail: {exc}")
        data = None
    finally:
        driver.close()
        driver.switch_to.window(main_handle)
        time.sleep(0.5)

    return data


# ---------------------------------------------------------------------------
# Page-by-page loop
# ---------------------------------------------------------------------------

def scrape_all_pages(driver: webdriver.Chrome, test: bool = False) -> list[dict]:
    all_records: list[dict] = []
    page_num = 1

    while True:
        print(f"\n[page {page_num}] Loading table...")
        _wait_for_table(driver)

        row_count = len(driver.find_elements(By.CSS_SELECTOR, TABLE_ROW_SELECTOR))
        print(f"[page {page_num}] {row_count} rows found.")

        rows_to_process = 1 if test else row_count
        for idx in range(rows_to_process):
            print(f"  Row {idx + 1}/{rows_to_process}...", end=" ", flush=True)
            record = process_row(driver, idx)
            if record:
                all_records.append(record)
                print("OK")
            else:
                print("SKIPPED")

        if test:
            print("\n[test] Test mode — stopping after 1 row.")
            break

        # --- Pagination ---
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, NEXT_PAGE_SELECTOR)
        except Exception:
            print("\n[pagination] No next-page button — done.")
            break

        classes  = next_btn.get_attribute("class") or ""
        href     = next_btn.get_attribute("href") or ""
        disabled = next_btn.get_attribute("disabled")
        if (
            "layui-disabled" in classes
            or "disabled" in classes
            or disabled is not None
            or href in ("javascript:;", "javascript:void(0)", "#", "")
            or not next_btn.is_displayed()
        ):
            print("\n[pagination] Last page reached (button disabled).")
            break

        try:
            first_row_text = driver.find_elements(
                By.CSS_SELECTOR, TABLE_ROW_SELECTOR
            )[0].text
        except Exception:
            first_row_text = ""

        next_btn.click()
        time.sleep(2)

        try:
            _wait_for_table(driver, timeout=8)
            new_first_row_text = driver.find_elements(
                By.CSS_SELECTOR, TABLE_ROW_SELECTOR
            )[0].text
        except Exception:
            new_first_row_text = first_row_text

        if new_first_row_text == first_row_text:
            print("\n[pagination] Page content unchanged after Next click — last page.")
            break

        page_num += 1

    return all_records


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def save_csv(records: list[dict], path: str) -> None:
    if not records:
        print("No data collected — CSV not written.")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"\n[output] {len(records)} records saved → {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Growatt end-user table scraper")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Scrape only the first row on the first page (for testing)",
    )
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Delete saved cookies and log in fresh",
    )
    args = parser.parse_args()

    if args.test:
        print("[test] Test mode enabled — will scrape 1 row only.")

    driver = setup_session(reset=args.reset_session)
    try:
        records = scrape_all_pages(driver, test=args.test)
        save_csv(records, OUTPUT_FILE)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
