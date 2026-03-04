"""
T0ckSn1per — Concurrent Tock reservation sniper
================================================
Spawns one browser session per (restaurant × day) combination so every
target date is polled simultaneously.  The first session that secures a
cart holds it open for 10 minutes (Tock's hold window) while notifying
the user to complete checkout.

Quick-start
-----------
1. pip install -r requirements.txt
2. Edit create_tasks() at the bottom of this file.
3. python t0cksn1per.py
"""

import json
import logging
import sys
import threading
import time
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)-30s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("t0cksn1per.log"),
    ],
)
log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

RESERVATION_TIME_FORMAT = "%I:%M %p"

# Tock login (not required — leave False to speed things up)
ENABLE_LOGIN   = False
TOCK_USERNAME  = "SET_YOUR_USER_NAME_HERE"
TOCK_PASSWORD  = "SET_YOUR_PASSWORD_HERE"

# Seconds between each poll cycle per worker (lower = faster, more load)
REFRESH_DELAY_SEC = 0.5

# Seconds to wait for page elements before declaring a timeout
PAGE_LOAD_TIMEOUT = 10

# How long to hold the browser open after securing a cart (Tock holds for 10 min)
BROWSER_HOLD_SEC = 600

# Stagger thread launches to avoid flooding Tock with simultaneous requests
THREAD_LAUNCH_DELAY_SEC = 0.5

# Luminati / residential proxy (only needed at very high poll rates)
ENABLE_PROXY   = False
USER_DATA_DIR  = "~/Library/Application Support/Google/Chrome"
PROFILE_DIR    = "Default"
EXTENSION_PATH = f"{USER_DATA_DIR}/{PROFILE_DIR}/Extensions/efohiadmkaogdhibjbmeppjpebenaool/1.149.316_0"

MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05",     "june": "06",     "july": "07",  "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# ── Notification ──────────────────────────────────────────────────────────────

def notify_user(message: str) -> None:
    """
    Alert the user that a reservation cart has been secured.
    Uses three channels: console banner, desktop notification, audible beep.
    """
    banner = "\n" + "=" * 60
    log.info(
        "%s\n  RESERVATION HELD — COMPLETE CHECKOUT NOW!\n\n%s\n"
        "  Cart held for %d minutes.\n%s",
        banner, message, BROWSER_HOLD_SEC // 60, banner,
    )

    # Desktop notification via plyer (optional dependency)
    try:
        from plyer import notification  # type: ignore
        notification.notify(
            title="T0ckSn1per — Reservation Found!",
            message=message,
            timeout=30,
        )
    except Exception:
        pass  # plyer not installed — console banner is enough

    # Audible alert
    try:
        import winsound  # type: ignore  # Windows only
        for _ in range(5):
            winsound.Beep(1000, 400)
    except Exception:
        try:
            for _ in range(5):
                sys.stdout.write("\a")
                sys.stdout.flush()
                time.sleep(0.3)
        except Exception:
            pass


# ── Task ──────────────────────────────────────────────────────────────────────

class Task:
    """
    Describes a single restaurant reservation search.

    Parameters
    ----------
    url           : Tock restaurant slug, e.g. "taneda"
    size          : Party size as a string, e.g. "2"
    year          : 4-digit year string, e.g. "2024"
    month         : Full month name, e.g. "September"
    days          : List of day strings to target, e.g. ["01", "14", "28"]
    earliest_time : Earliest acceptable time, e.g. "5:00 PM"
    latest_time   : Latest acceptable time,   e.g. "9:30 PM"
    """

    def __init__(
        self,
        url: str,
        size: str,
        year: str,
        month: str,
        days: list,
        earliest_time: str,
        latest_time: str,
    ):
        self.url           = url
        self.size          = size
        self.year          = year
        self.month         = month
        self.days          = days
        self.earliest_time = earliest_time
        self.latest_time   = latest_time

    def formatted_earliest(self) -> datetime:
        return datetime.strptime(self.earliest_time, RESERVATION_TIME_FORMAT)

    def formatted_latest(self) -> datetime:
        return datetime.strptime(self.latest_time, RESERVATION_TIME_FORMAT)

    def search_url(self) -> str:
        month_n = MONTH_NUM[self.month.lower()]
        return (
            f"https://www.exploretock.com/{self.url}/search"
            f"?date={self.year}-{month_n}-01&size={self.size}&time=19%3A00"
        )

    def __repr__(self) -> str:
        return json.dumps(self.__dict__)


# ── Per-day browser worker ────────────────────────────────────────────────────

class ReservationWorker:
    """
    Manages one Chrome session that polls a single (restaurant, day) pair.

    All workers spawned for the same Task share a ``found_event``.  When any
    worker clicks a time slot and secures a cart it:
      1. Sets ``found_event`` so the other workers stop immediately.
      2. Notifies the user.
      3. Sleeps for BROWSER_HOLD_SEC to keep the cart alive.
    """

    def __init__(self, task: Task, day: str, found_event: threading.Event):
        self.task        = task
        self.day         = day
        self.found_event = found_event
        self.driver      = self._make_driver()

    # ── Setup / teardown ─────────────────────────────────────────────────────

    def _make_driver(self) -> webdriver.Chrome:
        options = Options()
        if ENABLE_PROXY:
            options.add_argument(f"--load-extension={EXTENSION_PATH}")
            options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
            options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,900")
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT + 5)
        return driver

    def teardown(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self) -> None:
        log.info(
            "Worker started — %s | %s %s | party of %s | %s – %s",
            self.task.url, self.task.month, self.day, self.task.size,
            self.task.earliest_time, self.task.latest_time,
        )

        if ENABLE_LOGIN:
            self._login()

        try:
            while not self.found_event.is_set():
                time.sleep(REFRESH_DELAY_SEC)
                if self._poll():
                    # Claim victory before notifying so other workers stop ASAP
                    self.found_event.set()
                    msg = (
                        f"Restaurant : {self.task.url}\n"
                        f"  Date     : {self.task.month} {self.day}, {self.task.year}\n"
                        f"  Party    : {self.task.size}\n"
                        f"  Window   : {self.task.earliest_time} – {self.task.latest_time}"
                    )
                    notify_user(msg)
                    log.info(
                        "Holding browser open for %d s — complete checkout now!",
                        BROWSER_HOLD_SEC,
                    )
                    time.sleep(BROWSER_HOLD_SEC)
                    break
        except Exception as exc:
            log.error(
                "Worker error on %s day %s: %s", self.task.url, self.day, exc
            )
        finally:
            self.teardown()

    # ── Poll cycle ───────────────────────────────────────────────────────────

    def _poll(self) -> bool:
        """
        Load the search page and look for an available slot on self.day.
        Returns True when a slot has been clicked (cart held).
        """
        try:
            self.driver.get(self.task.search_url())
            WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT).until(
                expected_conditions.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.ConsumerCalendar-month")
                )
            )
        except TimeoutException:
            log.debug(
                "Calendar timeout for %s day %s — retrying", self.task.url, self.day
            )
            return False
        except WebDriverException as exc:
            log.warning("WebDriver error (%s day %s): %s", self.task.url, self.day, exc)
            return False

        return self._try_day()

    def _try_day(self) -> bool:
        """Find the target month on the calendar, then click the target day."""
        month_el = self._find_month()
        if month_el is None:
            return False

        for day_btn in month_el.find_elements(
            By.CSS_SELECTOR,
            "button.ConsumerCalendar-day.is-in-month.is-available",
        ):
            try:
                span = day_btn.find_element(By.CSS_SELECTOR, "span.B2")
            except Exception:
                continue

            if span.text.strip() == self.day:
                log.info(
                    "Day %s is available for %s — clicking", self.day, self.task.url
                )
                day_btn.click()
                return self._try_time()

        log.debug("Day %s not available for %s this cycle", self.day, self.task.url)
        return False

    def _find_month(self):
        """Return the calendar block for the target month, or None."""
        for month_el in self.driver.find_elements(
            By.CSS_SELECTOR, "div.ConsumerCalendar-month"
        ):
            try:
                heading = month_el.find_element(
                    By.CSS_SELECTOR,
                    "div.ConsumerCalendar-monthHeading span.H1",
                )
            except Exception:
                continue
            if self.task.month.lower() in heading.text.lower():
                return month_el

        log.debug(
            "Month %s not visible for %s", self.task.month, self.task.url
        )
        return None

    def _try_time(self) -> bool:
        """
        Wait for time slots to appear after a day is clicked, then click the
        first slot that falls within the acceptable time window.
        """
        try:
            WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT).until(
                expected_conditions.presence_of_element_located(
                    (By.CSS_SELECTOR, "button.Consumer-resultsListItem.is-available")
                )
            )
        except TimeoutException:
            log.debug(
                "No time slots loaded for %s day %s", self.task.url, self.day
            )
            return False

        for slot in self.driver.find_elements(
            By.CSS_SELECTOR, "button.Consumer-resultsListItem.is-available"
        ):
            try:
                time_span = slot.find_element(
                    By.CSS_SELECTOR,
                    "span.Consumer-resultsListItemTime span",
                )
                slot_time = datetime.strptime(
                    time_span.text.strip(), RESERVATION_TIME_FORMAT
                )
            except Exception:
                continue

            if self.task.formatted_earliest() <= slot_time <= self.task.formatted_latest():
                log.info(
                    "Clicking slot %s for %s day %s — cart will be held",
                    time_span.text, self.task.url, self.day,
                )
                slot.click()
                return True

        log.debug(
            "No slots in time window for %s day %s", self.task.url, self.day
        )
        return False

    def _login(self) -> None:
        self.driver.get("https://www.exploretock.com/login")
        WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT).until(
            expected_conditions.presence_of_element_located((By.NAME, "email"))
        )
        self.driver.find_element(By.NAME, "email").send_keys(TOCK_USERNAME)
        self.driver.find_element(By.NAME, "password").send_keys(TOCK_PASSWORD)
        self.driver.find_element(By.CSS_SELECTOR, ".Button").click()
        WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT).until(
            expected_conditions.visibility_of_element_located(
                (By.CSS_SELECTOR, ".MainHeader-accountName")
            )
        )


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_task(task: Task) -> None:
    """
    Spawn one ReservationWorker thread per target day for a single restaurant.
    All workers share a found_event — the first to secure a cart stops the rest.
    This function blocks until all workers have exited.
    """
    found_event = threading.Event()
    threads = []

    for day in task.days:
        worker = ReservationWorker(task, day, found_event)
        t = threading.Thread(
            target=worker.run,
            name=f"{task.url}-{task.month[:3]}-{day}",
            daemon=True,
        )
        threads.append(t)
        t.start()
        time.sleep(THREAD_LAUNCH_DELAY_SEC)

    for t in threads:
        t.join()

    log.info("Task complete for %s", task.url)


def run_all_tasks(tasks: list) -> None:
    """
    Run each restaurant Task in a dedicated thread so multiple restaurants
    are sniped concurrently.
    """
    restaurant_threads = []
    for task in tasks:
        t = threading.Thread(
            target=run_task,
            args=(task,),
            name=f"restaurant-{task.url}",
            daemon=True,
        )
        restaurant_threads.append(t)
        t.start()
        time.sleep(THREAD_LAUNCH_DELAY_SEC)

    for t in restaurant_threads:
        t.join()


# ── Task definitions ──────────────────────────────────────────────────────────

def create_tasks() -> list:
    """
    Define the restaurants and dates you want to snipe.

    Task(url, size, year, month, days, earliest_time, latest_time)
      url           — Tock restaurant slug
      size          — party size (string)
      year / month  — target year and full month name
      days          — list of day strings ("01"–"31") to watch
      earliest_time — lower bound of acceptable time window, e.g. "5:00 PM"
      latest_time   — upper bound of acceptable time window, e.g. "9:30 PM"
    """
    return [
        Task(
            url="tidal-seattle",
            size="2",
            year="2022",
            month="August",
            days=["23", "24", "25", "26", "27", "28", "29"],
            earliest_time="5:00 PM",
            latest_time="10:30 PM",
        ),
        Task(
            url="taneda",
            size="2",
            year="2022",
            month="September",
            days=[
                "01", "02", "03", "04", "07", "08", "09", "10",
                "11", "14", "15", "24", "25", "28", "29",
            ],
            earliest_time="5:00 PM",
            latest_time="8:30 PM",
        ),
    ]


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tasks = create_tasks()
    log.info("T0ckSn1per starting — %d restaurant task(s) loaded", len(tasks))
    log.info(
        "Total concurrent workers: %d",
        sum(len(t.days) for t in tasks),
    )
    run_all_tasks(tasks)
    log.info("All done.")
