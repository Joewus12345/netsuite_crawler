from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.util.retry import Retry


LOG = logging.getLogger("file_cabinet_downloader")
SCRIPT_VERSION = "2026.08.01-project-hierarchy-v4.1"
ACCOUNT_BASE_URL = "https://4891605.app.netsuite.com"
FILE_CABINET_PATH = "/app/common/media/mediaitemfolders.nl?sc=-63"
LOGIN_URL_MARKERS = (
    "/app/login/",
    "login.nl",
    "securityquestions.nl",
    "loginchallenge/entry.nl",
)
PART_SUFFIX = ".part"
MIN_SCHEDULER_WEIGHT = 16 * 1024 * 1024


class AuthenticationExpired(RuntimeError):
    pass


class InsufficientDiskSpace(RuntimeError):
    pass


@dataclass(frozen=True)
class FolderRecord:
    internal_id: str
    name: str
    size_text: str
    size_bytes: int
    last_modified: str
    download_url: str
    page_index: int
    parent_internal_id: str = ""
    root_internal_id: str = ""
    depth: int = 0
    relative_path: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_size(size_text: str) -> int:
    """Convert NetSuite's displayed folder size into a scheduling estimate."""
    text = " ".join((size_text or "").replace(",", "").split()).upper()
    if not text or text in {"-", "—"}:
        return 0

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)", text)
    if not match:
        raise ValueError(f"Unsupported NetSuite size: {size_text!r}")

    value = float(match.group(1))
    multiplier = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }[match.group(2)]
    return int(value * multiplier)


def safe_filename(value: str, max_length: int = 120) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "unnamed")[:max_length].rstrip(" .")


def parse_folder_rows(html: str, page_index: int, base_url: str) -> list[FolderRecord]:
    """Parse rows by their header labels, not brittle absolute XPath positions."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table#div__body")
    if table is None:
        raise RuntimeError("Could not find the File Cabinet table #div__body")

    headers = []
    for header in table.select("thead [data-label]"):
        headers.append((header.get("data-label") or header.get_text(" ", strip=True)).strip())
    header_indexes = {label.casefold(): index for index, label in enumerate(headers)}

    required = {"internal id", "name", "size", "last modified", "download"}
    missing = required.difference(header_indexes)
    if missing:
        raise RuntimeError(f"File Cabinet table is missing columns: {sorted(missing)}")

    records: list[FolderRecord] = []
    for row in table.select("tbody tr.uir-list-row-tr"):
        cells = row.select(":scope > td")
        if len(cells) < len(headers):
            continue

        def cell(label: str):
            return cells[header_indexes[label]]

        internal_id = cell("internal id").get_text(" ", strip=True)
        name_cell = cell("name")
        name = name_cell.get_text(" ", strip=True)
        size_text = cell("size").get_text(" ", strip=True)
        last_modified = cell("last modified").get_text(" ", strip=True)
        download_link = cell("download").select_one(
            "a[href*='/core/media/downloadfolder.nl']"
        )
        if not internal_id or download_link is None:
            continue

        records.append(
            FolderRecord(
                internal_id=internal_id,
                name=name,
                size_text=size_text,
                size_bytes=parse_size(size_text),
                last_modified=last_modified,
                download_url=urljoin(base_url, download_link["href"]),
                page_index=page_index,
            )
        )
    # NetSuite can briefly expose the same row twice while replacing the table
    # DOM. Preserve the first-seen order but inventory each Internal ID once.
    return list({record.internal_id: record for record in records}.values())


class DownloadDatabase:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS folders (
                internal_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                size_text TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                last_modified TEXT NOT NULL,
                download_url TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                download_path TEXT,
                actual_bytes INTEGER,
                sha256 TEXT,
                error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS folders_status_idx ON folders(status);
            CREATE TABLE IF NOT EXISTS scan_pages (
                page_index INTEGER PRIMARY KEY,
                row_count INTEGER NOT NULL,
                scanned_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS folder_scan_pages (
                folder_internal_id TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                scanned_at TEXT NOT NULL,
                PRIMARY KEY (folder_internal_id, page_index)
            );
            """
        )
        existing_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(folders)")
        }
        hierarchy_columns = {
            "parent_internal_id": "TEXT NOT NULL DEFAULT ''",
            "root_internal_id": "TEXT NOT NULL DEFAULT ''",
            "depth": "INTEGER NOT NULL DEFAULT 0",
            "relative_path": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in hierarchy_columns.items():
            if column not in existing_columns:
                self._connection.execute(
                    f"ALTER TABLE folders ADD COLUMN {column} {definition}"
                )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS folders_root_status_idx "
            "ON folders(root_internal_id, status)"
        )
        # Migrate state created by version 1: every full 50-row page already
        # present in the database was successfully scanned.
        self._connection.execute(
            """
            INSERT OR IGNORE INTO scan_pages (page_index, row_count, scanned_at)
            SELECT page_index, COUNT(*), ?
            FROM folders
            WHERE root_internal_id=''
            GROUP BY page_index
            HAVING COUNT(*) >= 50
            """,
            (utc_now(),),
        )
        self._connection.execute(
            "UPDATE folders SET status='pending', error='Interrupted before completion' "
            "WHERE status='downloading'"
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def upsert_inventory(
        self, records: Iterable[FolderRecord], scan_folder_id: str = ""
    ) -> None:
        records = list(records)
        if not records:
            return
        with self._lock:
            self._connection.executemany(
                """
                INSERT INTO folders (
                    internal_id, name, size_text, size_bytes, last_modified,
                    download_url, page_index, parent_internal_id,
                    root_internal_id, depth, relative_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_id) DO UPDATE SET
                    name=excluded.name,
                    size_text=excluded.size_text,
                    size_bytes=excluded.size_bytes,
                    last_modified=excluded.last_modified,
                    download_url=excluded.download_url,
                    page_index=excluded.page_index,
                    parent_internal_id=excluded.parent_internal_id,
                    root_internal_id=excluded.root_internal_id,
                    depth=excluded.depth,
                    relative_path=excluded.relative_path,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        item.internal_id,
                        item.name,
                        item.size_text,
                        item.size_bytes,
                        item.last_modified,
                        item.download_url,
                        item.page_index,
                        item.parent_internal_id,
                        item.root_internal_id,
                        item.depth,
                        item.relative_path,
                        utc_now(),
                    )
                    for item in records
                ],
            )
            if scan_folder_id:
                self._connection.execute(
                    """
                    INSERT INTO folder_scan_pages (
                        folder_internal_id, page_index, row_count, scanned_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(folder_internal_id, page_index) DO UPDATE SET
                        row_count=excluded.row_count,
                        scanned_at=excluded.scanned_at
                    """,
                    (scan_folder_id, records[0].page_index, len(records), utc_now()),
                )
            else:
                self._connection.execute(
                """
                INSERT INTO scan_pages (page_index, row_count, scanned_at)
                VALUES (?, ?, ?)
                ON CONFLICT(page_index) DO UPDATE SET
                    row_count=excluded.row_count,
                    scanned_at=excluded.scanned_at
                """,
                (records[0].page_index, len(records), utc_now()),
                )
            self._connection.commit()

    def scanned_pages(self, folder_internal_id: str = "") -> set[int]:
        with self._lock:
            if folder_internal_id:
                rows = self._connection.execute(
                    "SELECT page_index FROM folder_scan_pages "
                    "WHERE folder_internal_id=?",
                    (folder_internal_id,),
                )
            else:
                rows = self._connection.execute("SELECT page_index FROM scan_pages")
            return {
                int(row["page_index"])
                for row in rows
            }

    def clear_scan_pages(self, folder_internal_id: str = "") -> None:
        with self._lock:
            if folder_internal_id:
                self._connection.execute(
                    "DELETE FROM folder_scan_pages WHERE folder_internal_id=?",
                    (folder_internal_id,),
                )
            else:
                self._connection.execute("DELETE FROM scan_pages")
            self._connection.commit()

    def mark_folder_page_scanned(
        self, folder_internal_id: str, page_index: int, row_count: int
    ) -> None:
        """Checkpoint a hierarchy page even when it contains files but no folders."""
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO folder_scan_pages (
                    folder_internal_id, page_index, row_count, scanned_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(folder_internal_id, page_index) DO UPDATE SET
                    row_count=excluded.row_count,
                    scanned_at=excluded.scanned_at
                """,
                (folder_internal_id, page_index, row_count, utc_now()),
            )
            self._connection.commit()

    def pending(
        self,
        retry_failed: bool,
        min_size_bytes: int = 0,
        max_size_bytes: int = 0,
        root_folder_id: str = "",
    ) -> list[sqlite3.Row]:
        statuses = ("pending", "failed") if retry_failed else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        size_conditions = ["size_bytes >= ?"]
        parameters: list[object] = [*statuses, min_size_bytes]
        if max_size_bytes > 0:
            size_conditions.append("size_bytes <= ?")
            parameters.append(max_size_bytes)
        root_condition = ""
        if root_folder_id:
            root_condition = "AND root_internal_id = ?"
            parameters.append(root_folder_id)
        with self._lock:
            return list(
                self._connection.execute(
                    f"""
                    SELECT * FROM folders
                    WHERE status IN ({placeholders})
                      AND {' AND '.join(size_conditions)}
                      {root_condition}
                    ORDER BY
                        CASE WHEN size_bytes >= 805306368 THEN 1 ELSE 0 END,
                        page_index,
                        CAST(internal_id AS INTEGER)
                    """,
                    parameters,
                )
            )

    def mark_split_ready(self, internal_id: str) -> None:
        self._update(
            internal_id,
            "status='split_ready', error=NULL, updated_at=?",
            (utc_now(),),
        )

    def hierarchy_count(self, root_folder_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM folders WHERE root_internal_id=?",
                (root_folder_id,),
            ).fetchone()
            return int(row["count"])

    def mark_started(self, internal_id: str) -> None:
        self._update(
            internal_id,
            "status='downloading', attempts=attempts+1, error=NULL, updated_at=?",
            (utc_now(),),
        )

    def mark_complete(
        self, internal_id: str, path: Path, actual_bytes: int, sha256: str
    ) -> None:
        self._update(
            internal_id,
            """
            status='complete', download_path=?, actual_bytes=?, sha256=?,
            error=NULL, updated_at=?
            """,
            (str(path), actual_bytes, sha256, utc_now()),
        )

    def mark_failed(self, internal_id: str, error: str) -> None:
        self._update(
            internal_id,
            "status='failed', error=?, updated_at=?",
            (error[:2000], utc_now()),
        )

    def _update(self, internal_id: str, expression: str, values: tuple) -> None:
        with self._lock:
            self._connection.execute(
                f"UPDATE folders SET {expression} WHERE internal_id=?",
                (*values, internal_id),
            )
            self._connection.commit()

    def summary(self, root_folder_id: str = "") -> dict[str, int]:
        with self._lock:
            if root_folder_id:
                rows = self._connection.execute(
                    "SELECT status, COUNT(*) AS count FROM folders "
                    "WHERE root_internal_id=? GROUP BY status",
                    (root_folder_id,),
                )
            else:
                rows = self._connection.execute(
                    "SELECT status, COUNT(*) AS count FROM folders GROUP BY status"
                )
            return {row["status"]: row["count"] for row in rows}

    def inventory_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM folders"
            ).fetchone()
            return int(row["count"])

    def export_csv(
        self,
        path: Path,
        root_folder_id: str = "",
        statuses: tuple[str, ...] = (),
    ) -> None:
        import csv

        conditions: list[str] = []
        parameters: list[object] = []
        if root_folder_id:
            conditions.append("root_internal_id=?")
            parameters.append(root_folder_id)
        if statuses:
            conditions.append(
                "status IN (" + ",".join("?" for _ in statuses) + ")"
            )
            parameters.extend(statuses)
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._lock:
            rows = list(
                self._connection.execute(
                    f"SELECT * FROM folders {where_clause} "
                    "ORDER BY page_index, CAST(internal_id AS INTEGER)",
                    parameters,
                )
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(rows[0].keys() if rows else [])
            writer.writerows([tuple(row) for row in rows])


class WeightedLimiter:
    """Bound simultaneous transfers by count and expected in-flight bytes."""

    def __init__(self, byte_capacity: int, large_threshold: int):
        self.byte_capacity = byte_capacity
        self.large_threshold = large_threshold
        self.available = byte_capacity
        self._condition = threading.Condition()

    def weight(self, expected_bytes: int) -> int:
        if expected_bytes >= self.large_threshold:
            return self.byte_capacity
        return min(
            self.byte_capacity,
            max(expected_bytes, MIN_SCHEDULER_WEIGHT),
        )

    def acquire(self, expected_bytes: int) -> int:
        weight = self.weight(expected_bytes)
        with self._condition:
            self._condition.wait_for(lambda: self.available >= weight)
            self.available -= weight
        return weight

    def release(self, weight: int) -> None:
        with self._condition:
            self.available += weight
            self._condition.notify_all()


def create_requests_session(cookies: list[dict], user_agent: str) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=4))
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/zip,application/octet-stream,*/*",
        }
    )
    for cookie in cookies:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    return session


def ensure_disk_space(directory: Path, expected_bytes: int, reserve_bytes: int) -> None:
    free_bytes = shutil.disk_usage(directory).free
    required = max(expected_bytes, MIN_SCHEDULER_WEIGHT) + reserve_bytes
    if free_bytes < required:
        raise InsufficientDiskSpace(
            f"Only {free_bytes:,} bytes free; at least {required:,} are required"
        )


def validate_zip(path: Path, verify_mode: str) -> None:
    if not zipfile.is_zipfile(path):
        raise RuntimeError("Downloaded response is not a valid ZIP archive")
    with zipfile.ZipFile(path) as archive:
        archive.infolist()  # Forces the central directory to be read.
        if verify_mode == "crc":
            bad_member = archive.testzip()
            if bad_member:
                raise RuntimeError(f"ZIP CRC failed for member {bad_member!r}")


def hash_existing_file(path: Path, chunk_bytes: int) -> hashlib._Hash:
    digest = hashlib.sha256()
    if path.exists():
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_bytes):
                digest.update(chunk)
    return digest


def download_transfer(
    session: requests.Session,
    download_url: str,
    referer: str,
    part_path: Path,
    stop_event: threading.Event,
    chunk_bytes: int,
    connect_timeout: int,
    read_timeout: int,
    verify_mode: str,
    internal_id: str,
) -> tuple[int, str]:
    existing_bytes = part_path.stat().st_size if part_path.exists() else 0
    headers = {"Referer": referer}
    if existing_bytes:
        headers["Range"] = f"bytes={existing_bytes}-"
        LOG.info(
            "[%s] Found %.2f GiB partial file; requesting resume",
            internal_id,
            existing_bytes / 1024**3,
        )

    with session.get(
        download_url,
        headers=headers,
        stream=True,
        allow_redirects=True,
        timeout=(connect_timeout, read_timeout),
    ) as response:
        final_url = response.url.lower()
        content_type = response.headers.get("Content-Type", "").lower()
        if any(marker in final_url for marker in LOGIN_URL_MARKERS):
            raise AuthenticationExpired("NetSuite redirected the download to login")
        if "text/html" in content_type:
            raise AuthenticationExpired(
                "NetSuite returned HTML instead of a folder ZIP; the session may have expired"
            )

        # A 416 can mean the local partial already contains the complete remote
        # object. Accept it only if the existing file is independently valid.
        if response.status_code == 416 and existing_bytes:
            validate_zip(part_path, verify_mode)
            return existing_bytes, hash_existing_file(
                part_path, chunk_bytes
            ).hexdigest()

        response.raise_for_status()
        resumed = response.status_code == 206 and existing_bytes > 0
        if resumed:
            content_range = response.headers.get("Content-Range", "")
            match = re.match(r"bytes\s+(\d+)-\d+/(?:\d+|\*)", content_range)
            if not match or int(match.group(1)) != existing_bytes:
                raise RuntimeError(
                    f"Unsafe Content-Range for resume: {content_range!r}"
                )
            mode = "ab"
            actual_bytes = existing_bytes
            digest = hash_existing_file(part_path, chunk_bytes)
            LOG.info("[%s] NetSuite accepted HTTP Range resume", internal_id)
        else:
            # NetSuite may ignore Range and return 200. Starting over is safe;
            # appending that response would corrupt the archive.
            mode = "wb"
            actual_bytes = 0
            digest = hashlib.sha256()
            if existing_bytes:
                LOG.info(
                    "[%s] NetSuite did not accept resume; restarting this folder",
                    internal_id,
                )

        content_length = int(response.headers.get("Content-Length", "0") or 0)
        expected_final_bytes = (
            actual_bytes + content_length if content_length else 0
        )
        next_progress_log = time.monotonic() + 60
        with part_path.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=chunk_bytes):
                if stop_event.is_set():
                    raise RuntimeError("Download run stopped")
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                actual_bytes += len(chunk)
                if time.monotonic() >= next_progress_log:
                    LOG.info(
                        "[%s] Downloaded %.2f GiB so far",
                        internal_id,
                        actual_bytes / 1024**3,
                    )
                    next_progress_log = time.monotonic() + 60
            handle.flush()
            os.fsync(handle.fileno())

    if expected_final_bytes and actual_bytes != expected_final_bytes:
        raise RuntimeError(
            f"Content-Length mismatch: expected final size "
            f"{expected_final_bytes}, got {actual_bytes}"
        )
    try:
        validate_zip(part_path, verify_mode)
    except Exception:
        # A fully transferred but invalid response is not a resumable prefix.
        part_path.unlink(missing_ok=True)
        raise
    return actual_bytes, digest.hexdigest()


def download_one(
    row: sqlite3.Row,
    db: DownloadDatabase,
    output_dir: Path,
    cookies: list[dict],
    user_agent: str,
    referer: str,
    limiter: WeightedLimiter,
    reserve_bytes: int,
    verify_mode: str,
    stop_event: threading.Event,
    chunk_bytes: int,
    connect_timeout: int,
    read_timeout: int,
    folder_retries: int,
    retry_backoff: int,
) -> Path:
    if stop_event.is_set():
        raise RuntimeError("Download run stopped")

    internal_id = row["internal_id"]
    expected_bytes = int(row["size_bytes"])
    weight = limiter.acquire(expected_bytes)
    destination_dir = output_dir
    if row["relative_path"]:
        destination_dir = output_dir / Path(row["relative_path"])
    destination_dir.mkdir(parents=True, exist_ok=True)
    final_path = destination_dir / (
        f"{internal_id}__{safe_filename(row['name'])}.zip"
    )
    part_path = final_path.with_suffix(final_path.suffix + PART_SUFFIX)
    session = create_requests_session(cookies, user_agent)

    try:
        ensure_disk_space(destination_dir, expected_bytes, reserve_bytes)
        db.mark_started(internal_id)
        actual_bytes = 0
        sha256 = ""
        for attempt in range(1, folder_retries + 1):
            try:
                LOG.info(
                    "[%s] Starting %s (%s), attempt %d/%d",
                    internal_id,
                    row["name"],
                    row["size_text"],
                    attempt,
                    folder_retries,
                )
                actual_bytes, sha256 = download_transfer(
                    session=session,
                    download_url=row["download_url"],
                    referer=referer,
                    part_path=part_path,
                    stop_event=stop_event,
                    chunk_bytes=chunk_bytes,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    verify_mode=verify_mode,
                    internal_id=internal_id,
                )
                break
            except AuthenticationExpired:
                raise
            except Exception as exc:
                if attempt >= folder_retries:
                    raise
                delay = retry_backoff * attempt
                LOG.warning(
                    "[%s] Attempt %d failed: %s. Retrying in %d seconds",
                    internal_id,
                    attempt,
                    exc,
                    delay,
                )
                stop_event.wait(delay)

        os.replace(part_path, final_path)
        db.mark_complete(internal_id, final_path, actual_bytes, sha256)
        LOG.info("[%s] Complete: %s", internal_id, final_path.name)
        return final_path
    except AuthenticationExpired:
        stop_event.set()
        db.mark_failed(internal_id, "Authentication expired")
        raise
    except Exception as exc:
        db.mark_failed(internal_id, str(exc))
        LOG.error("[%s] Failed: %s", internal_id, exc)
        raise
    finally:
        session.close()
        limiter.release(weight)


def current_row_signature(driver: webdriver.Chrome) -> str:
    """Read the first rows atomically while NetSuite replaces the table DOM."""
    try:
        return (
            driver.execute_script(
                """
                return Array.from(
                    document.querySelectorAll(
                        '#div__body tbody tr.uir-list-row-tr'
                    )
                )
                .slice(0, 3)
                .map(row => (row.innerText || row.textContent || '')
                    .replace(/\\s+/g, ' ')
                    .trim()
                    .slice(0, 200))
                .join('|');
                """
            )
            or ""
        )
    except (StaleElementReferenceException, WebDriverException):
        return ""


def wait_for_table(driver: webdriver.Chrome, timeout: int = 40) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: len(
            d.find_elements(
                By.CSS_SELECTOR, "#div__body tbody tr.uir-list-row-tr"
            )
        )
        > 0
    )


def page_count(driver: webdriver.Chrome) -> int:
    options = driver.find_elements(By.CSS_SELECTOR, "#segment_sel_fs li")
    return max(1, len(options))


def selected_page_index(driver: webdriver.Chrome) -> int:
    try:
        value = driver.execute_script(
            """
            const wrapper = document.getElementById('segment_sel_fs');
            return wrapper ? wrapper.getAttribute('data-selected') : null;
            """
        )
        return int(value) if value not in (None, "") else -1
    except (TypeError, ValueError, WebDriverException):
        return -1


def go_to_page(
    driver: webdriver.Chrome,
    page_index: int,
    timeout: int = 60,
    attempts: int = 3,
) -> None:
    if selected_page_index(driver) == page_index and current_row_signature(driver):
        return

    selector = f"#segment_sel_fs li:nth-child({page_index + 1}) a"
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        before = current_row_signature(driver)
        try:
            link = WebDriverWait(
                driver,
                timeout,
                ignored_exceptions=(
                    NoSuchElementException,
                    StaleElementReferenceException,
                ),
            ).until(lambda d: d.find_element(By.CSS_SELECTOR, selector))
            driver.execute_script("arguments[0].click();", link)

            def page_finished_loading(d):
                try:
                    signature = current_row_signature(d)
                    return (
                        selected_page_index(d) == page_index
                        and bool(signature)
                        and signature != before
                    )
                except (StaleElementReferenceException, WebDriverException):
                    return False

            WebDriverWait(
                driver,
                timeout,
                poll_frequency=0.35,
                ignored_exceptions=(
                    NoSuchElementException,
                    StaleElementReferenceException,
                ),
            ).until(page_finished_loading)
            return
        except (
            TimeoutException,
            NoSuchElementException,
            StaleElementReferenceException,
            WebDriverException,
        ) as exc:
            last_error = exc
            LOG.warning(
                "Page %d navigation attempt %d/%d failed; retrying",
                page_index + 1,
                attempt,
                attempts,
            )
            try:
                wait_for_table(driver, min(timeout, 30))
            except TimeoutException:
                driver.refresh()
                wait_for_table(driver, timeout)

    raise TimeoutException(
        f"Could not load File Cabinet page {page_index + 1} after {attempts} attempts"
    ) from last_error


def scan_inventory(
    driver: webdriver.Chrome,
    db: DownloadDatabase,
    base_url: str,
    timeout: int,
    resume: bool = True,
) -> int:
    wait_for_table(driver, timeout)
    total_pages = page_count(driver)
    scanned_pages = db.scanned_pages() if resume else set()
    total_seen: set[str] = set()

    for page_index in range(total_pages):
        if page_index in scanned_pages:
            LOG.info(
                "Skipping previously scanned page %d/%d",
                page_index + 1,
                total_pages,
            )
            continue
        go_to_page(driver, page_index, timeout)
        records = parse_folder_rows(driver.page_source, page_index, base_url)
        if not records:
            raise RuntimeError(f"No folder rows found on page {page_index + 1}")
        db.upsert_inventory(records)
        total_seen.update(record.internal_id for record in records)
        LOG.info(
            "Scanned page %d/%d: %d folders (%d unique total)",
            page_index + 1,
            total_pages,
            len(records),
            len(total_seen),
        )
    return len(total_seen)


def active_folder_id(driver: webdriver.Chrome) -> str:
    """Return the folder currently shown in the right-hand File Cabinet pane."""
    try:
        return str(
            driver.execute_script(
                """
                const field = document.querySelector(
                    'input#folder, input[name="folder"]'
                );
                if (field && field.value) return String(field.value);
                const heading = document.querySelector(
                    '#medialisthdr_t a[href*="showFolderContents"]'
                );
                const match = heading && heading.getAttribute('href')
                    .match(/[0-9]+/);
                return match ? match[1] : '';
                """
            )
            or ""
        )
    except WebDriverException:
        return ""


def open_folder_in_cabinet(
    driver: webdriver.Chrome, folder_internal_id: str, timeout: int
) -> None:
    """Use NetSuite's own UI function; relative row hrefs are not real URLs."""
    before = current_row_signature(driver)
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script(
            "return typeof window.showFolderContents === 'function'"
        )
    )
    driver.execute_script(
        "window.showFolderContents(arguments[0]);", str(folder_internal_id)
    )

    def folder_loaded(d):
        signature = current_row_signature(d)
        return (
            active_folder_id(d) == str(folder_internal_id)
            and bool(signature)
            and signature != before
        )

    WebDriverWait(
        driver,
        timeout,
        poll_frequency=0.35,
        ignored_exceptions=(
            NoSuchElementException,
            StaleElementReferenceException,
            WebDriverException,
        ),
    ).until(folder_loaded)


def expand_folder_inventory(
    driver: webdriver.Chrome,
    db: DownloadDatabase,
    base_url: str,
    folder_internal_id: str,
    folder_name: str,
    timeout: int,
    resume: bool = True,
) -> int:
    """Inventory direct child folders so a large parent can be downloaded in pieces."""
    open_folder_in_cabinet(driver, folder_internal_id, timeout)
    total_pages = page_count(driver)
    scanned_pages = (
        db.scanned_pages(folder_internal_id) if resume else set()
    )
    total_seen: set[str] = set()
    relative_path = f"{folder_internal_id}__{safe_filename(folder_name)}"

    for page_index in range(total_pages):
        if page_index in scanned_pages:
            LOG.info(
                "Skipping previously scanned %s page %d/%d",
                folder_name,
                page_index + 1,
                total_pages,
            )
            continue
        go_to_page(driver, page_index, timeout)
        parsed = parse_folder_rows(driver.page_source, page_index, base_url)
        if not parsed:
            # The pagination total includes ordinary files as well as folders.
            # A page with no /downloadfolder.nl links is therefore valid.
            db.mark_folder_page_scanned(str(folder_internal_id), page_index, 0)
            LOG.info(
                "Scanned %s page %d/%d: 0 child folders (file-only page)",
                folder_name,
                page_index + 1,
                total_pages,
            )
            continue
        records = [
            FolderRecord(
                internal_id=item.internal_id,
                name=item.name,
                size_text=item.size_text,
                size_bytes=item.size_bytes,
                last_modified=item.last_modified,
                download_url=item.download_url,
                page_index=item.page_index,
                parent_internal_id=str(folder_internal_id),
                root_internal_id=str(folder_internal_id),
                depth=1,
                relative_path=relative_path,
            )
            for item in parsed
        ]
        db.upsert_inventory(records, scan_folder_id=str(folder_internal_id))
        total_seen.update(record.internal_id for record in records)
        LOG.info(
            "Scanned %s page %d/%d: %d children (%d this run)",
            folder_name,
            page_index + 1,
            total_pages,
            len(records),
            len(total_seen),
        )

    db.mark_split_ready(str(folder_internal_id))
    return db.hierarchy_count(str(folder_internal_id))


def create_driver(download_dir: Path, headless: bool, profile_dir: str | None):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")
    if profile_dir:
        options.add_argument(f"--user-data-dir={Path(profile_dir).resolve()}")
        options.add_argument("--profile-directory=Default")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )
    return webdriver.Chrome(options=options)


def ensure_authenticated(driver: webdriver.Chrome, base_url: str) -> None:
    """
    Reuse the existing crawler.py login/session logic when available.
    The module is intentionally optional so this downloader can also use a
    persistent Chrome profile that is already authenticated.
    """
    try:
        import crawler
    except ImportError:
        crawler = None

    if crawler and hasattr(crawler, "is_logged_in") and crawler.is_logged_in(driver):
        return
    if crawler and hasattr(crawler, "login"):
        result = crawler.login(driver)
        if result is False:
            raise RuntimeError("Existing crawler.login() reported failure")
        return

    driver.get(f"{base_url}/app/center/card.nl?whence=")
    if any(marker in driver.current_url.lower() for marker in LOGIN_URL_MARKERS):
        if driver.capabilities.get("goog:chromeOptions", {}).get("args", []).count(
            "--headless=new"
        ):
            raise RuntimeError(
                "Login is required. Run without --headless or connect crawler.py."
            )
        input("Log in to NetSuite in Chrome, then press Enter here...")


def run_downloads(
    db: DownloadDatabase,
    driver: webdriver.Chrome,
    output_dir: Path,
    workers: int,
    in_flight_bytes: int,
    large_threshold_bytes: int,
    reserve_bytes: int,
    retry_failed: bool,
    max_downloads: int,
    min_size_bytes: int,
    max_size_bytes: int,
    verify_mode: str,
    chunk_bytes: int,
    connect_timeout: int,
    read_timeout: int,
    folder_retries: int,
    retry_backoff: int,
    root_folder_id: str = "",
) -> None:
    rows = db.pending(
        retry_failed,
        min_size_bytes=min_size_bytes,
        max_size_bytes=max_size_bytes,
        root_folder_id=root_folder_id,
    )
    if max_downloads > 0:
        rows = rows[:max_downloads]
    if not rows:
        LOG.info("No pending downloads.")
        return

    cookies = driver.get_cookies()
    user_agent = driver.execute_script("return navigator.userAgent")
    referer = driver.current_url
    limiter = WeightedLimiter(in_flight_bytes, large_threshold_bytes)
    stop_event = threading.Event()

    LOG.info(
        "Starting %d pending folders with %d workers and %.2f GiB byte budget",
        len(rows),
        workers,
        in_flight_bytes / 1024**3,
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                download_one,
                row,
                db,
                output_dir,
                cookies,
                user_agent,
                referer,
                limiter,
                reserve_bytes,
                verify_mode,
                stop_event,
                chunk_bytes,
                connect_timeout,
                read_timeout,
                folder_retries,
                retry_backoff,
            )
            for row in rows
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except AuthenticationExpired:
                LOG.error(
                    "Authentication expired. Remaining work will stay resumable."
                )
            except Exception:
                pass


def gib(value: float) -> int:
    return int(value * 1024**3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resumable NetSuite File Cabinet folder downloader"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument(
        "mode",
        choices=("scan", "expand", "download", "all", "status"),
        nargs="?",
        default="all",
    )
    parser.add_argument("--base-url", default=ACCOUNT_BASE_URL)
    parser.add_argument(
        "--file-cabinet-path",
        default=FILE_CABINET_PATH,
        help="Do not include volatile siaT/siaWhc/siaNv parameters",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("file_cabinet_downloads"))
    parser.add_argument("--state-db", type=Path, default=Path("file_cabinet_state.sqlite3"))
    parser.add_argument("--manifest-csv", type=Path, default=Path("file_cabinet_manifest.csv"))
    parser.add_argument(
        "--failed-csv",
        type=Path,
        default=Path("failed_downloads.csv"),
        help="Filtered failed-download report written after status/download runs",
    )
    parser.add_argument(
        "--folder-id",
        default="",
        help="Parent folder Internal ID to inventory in expand mode (for Projects: 6225)",
    )
    parser.add_argument(
        "--folder-name",
        default="",
        help="Readable parent name used for logs and the output subdirectory",
    )
    parser.add_argument(
        "--root-folder-id",
        default="",
        help="Download only descendants inventoried under this parent ID",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--in-flight-gib", type=float, default=1.5)
    parser.add_argument("--large-threshold-gib", type=float, default=0.75)
    parser.add_argument("--reserve-disk-gib", type=float, default=10.0)
    parser.add_argument("--chunk-mib", type=int, default=4)
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=60,
        help="Seconds allowed to establish a download connection",
    )
    parser.add_argument(
        "--read-timeout",
        type=int,
        default=900,
        help="Maximum seconds between received chunks",
    )
    parser.add_argument(
        "--folder-retries",
        type=int,
        default=3,
        help="Attempts per folder during one run",
    )
    parser.add_argument(
        "--retry-backoff",
        type=int,
        default=30,
        help="Base seconds between folder attempts",
    )
    parser.add_argument("--verify", choices=("structure", "crc"), default="structure")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=0,
        help="Stop after this many queued folders; 0 means no limit",
    )
    parser.add_argument(
        "--min-size-gib",
        type=float,
        default=0,
        help="Only process folders at or above this displayed size",
    )
    parser.add_argument(
        "--max-size-gib",
        type=float,
        default=0,
        help="Only process folders at or below this displayed size; 0 means unlimited",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--profile-dir")
    parser.add_argument("--page-timeout", type=int, default=60)
    parser.add_argument(
        "--rescan-all",
        action="store_true",
        help="Ignore completed page checkpoints and rebuild the full inventory",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.workers < 1 or args.workers > 6:
        raise SystemExit("--workers must be between 1 and 6")
    if args.in_flight_gib <= 0 or args.large_threshold_gib <= 0:
        raise SystemExit("Byte-budget options must be greater than zero")
    if args.max_downloads < 0:
        raise SystemExit("--max-downloads cannot be negative")
    if args.min_size_gib < 0 or args.max_size_gib < 0:
        raise SystemExit("Size filters cannot be negative")
    if (
        args.max_size_gib > 0
        and args.max_size_gib < args.min_size_gib
    ):
        raise SystemExit("--max-size-gib cannot be below --min-size-gib")
    if args.connect_timeout < 1 or args.read_timeout < 1:
        raise SystemExit("Connection and read timeouts must be positive")
    if args.folder_retries < 1 or args.folder_retries > 10:
        raise SystemExit("--folder-retries must be between 1 and 10")
    if args.retry_backoff < 0:
        raise SystemExit("--retry-backoff cannot be negative")
    if args.mode == "expand" and not args.folder_id:
        raise SystemExit("expand mode requires --folder-id")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.state_db.parent.mkdir(parents=True, exist_ok=True)
    db = DownloadDatabase(args.state_db)

    if args.mode == "status":
        print(json.dumps(db.summary(args.root_folder_id), indent=2))
        db.export_csv(args.manifest_csv, root_folder_id=args.root_folder_id)
        db.export_csv(
            args.failed_csv,
            root_folder_id=args.root_folder_id,
            statuses=("failed",),
        )
        db.close()
        return 0

    driver = create_driver(args.output_dir, args.headless, args.profile_dir)
    try:
        ensure_authenticated(driver, args.base_url)
        driver.get(urljoin(args.base_url, args.file_cabinet_path))
        wait_for_table(driver, args.page_timeout)

        if args.mode in {"scan", "all"}:
            if args.rescan_all:
                db.clear_scan_pages()
            scan_inventory(
                driver,
                db,
                args.base_url,
                args.page_timeout,
                resume=not args.rescan_all,
            )
            LOG.info(
                "Inventory complete: %d unique folders",
                db.inventory_count(),
            )
            db.export_csv(args.manifest_csv)

        if args.mode == "expand":
            folder_name = args.folder_name or f"folder_{args.folder_id}"
            if args.rescan_all:
                db.clear_scan_pages(args.folder_id)
            child_count = expand_folder_inventory(
                driver=driver,
                db=db,
                base_url=args.base_url,
                folder_internal_id=args.folder_id,
                folder_name=folder_name,
                timeout=args.page_timeout,
                resume=not args.rescan_all,
            )
            LOG.info(
                "Expansion complete: %d direct children under %s (%s)",
                child_count,
                folder_name,
                args.folder_id,
            )
            db.export_csv(
                args.manifest_csv,
                root_folder_id=args.folder_id,
            )

        if args.mode in {"download", "all"}:
            run_downloads(
                db=db,
                driver=driver,
                output_dir=args.output_dir,
                workers=args.workers,
                in_flight_bytes=gib(args.in_flight_gib),
                large_threshold_bytes=gib(args.large_threshold_gib),
                reserve_bytes=gib(args.reserve_disk_gib),
                retry_failed=args.retry_failed,
                max_downloads=args.max_downloads,
                min_size_bytes=gib(args.min_size_gib),
                max_size_bytes=gib(args.max_size_gib),
                verify_mode=args.verify,
                chunk_bytes=args.chunk_mib * 1024**2,
                connect_timeout=args.connect_timeout,
                read_timeout=args.read_timeout,
                folder_retries=args.folder_retries,
                retry_backoff=args.retry_backoff,
                root_folder_id=args.root_folder_id,
            )
            db.export_csv(
                args.manifest_csv,
                root_folder_id=args.root_folder_id,
            )
            db.export_csv(
                args.failed_csv,
                root_folder_id=args.root_folder_id,
                statuses=("failed",),
            )

        LOG.info("Final status: %s", db.summary(args.root_folder_id))
        return 0
    except TimeoutException as exc:
        LOG.error("Timed out waiting for the File Cabinet UI: %s", exc)
        return 2
    finally:
        driver.quit()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
