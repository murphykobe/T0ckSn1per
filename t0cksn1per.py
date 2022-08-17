import threading
import time
import json

from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.chrome.options import Options

# Login not required for Tock. Leave it as false to decrease reservation delay
ENABLE_LOGIN = False
TOCK_USERNAME = "SET_YOUR_USER_NAME_HERE"
TOCK_PASSWORD = "SET_YOUR_PASSWORD_HERE"

RESERVATION_TIME_FORMAT = "%I:%M %p"

# Set the time range for acceptable reservation times.
# I.e., any available slots between 5:00 PM and 8:30 PM

# Multithreading configurations
THREAD_DELAY_SEC = 1

# Time between each page refresh in milliseconds. Decrease this time to
# increase the number of reservation attempts
REFRESH_DELAY_MSEC = 500

# Chrome extension configurations that are used with Luminati.io proxy.
# Enable proxy to avoid getting IP potentially banned. This should be enabled only if the REFRESH_DELAY_MSEC
# is extremely low (sub hundred) and NUM_THREADS > 1.
ENABLE_PROXY = False
USER_DATA_DIR = '~/Library/Application Support/Google/Chrome'
PROFILE_DIR = 'Default'
# https://chrome.google.com/webstore/detail/luminati/efohiadmkaogdhibjbmeppjpebenaool
EXTENSION_PATH = USER_DATA_DIR + '/' + PROFILE_DIR + '/Extensions/efohiadmkaogdhibjbmeppjpebenaool/1.149.316_0'

# Delay for how long the browser remains open so that the reservation can be finalized. Tock holds the reservation
# for 10 minutes before releasing.
BROWSER_CLOSE_DELAY_SEC = 600

WEBDRIVER_TIMEOUT_DELAY_MS = 3000

MONTH_NUM = {
    'january':   '01',
    'february':  '02',
    'march':     '03',
    'april':     '04',
    'may':       '05',
    'june':      '06',
    'july':      '07',
    'august':    '08',
    'september': '09',
    'october':   '10',
    'november':  '11',
    'december':  '12'
}

# Class to holds task data, for thread running different task
class Task():

    def __init__(self, url, size, year, month, days, earlist_time, latest_time):
        # Reservation store url e.g. taneda
        self.url = url
        # Set the party size for the reservation, e.g "4"
        self.size = size

        # Set your specific reservation year month and days, e.g "2022" "August" ['04', '28', '29']
        self.year = year
        self.month = month
        self.days = days
        # Set the time range for acceptable reservation times.
        # I.e., any available slots between 5:00 PM and 8:30 PM then "5:00 PM" and "8:30 PM"
        self.earlist_time = earlist_time
        self.latest_time = latest_time
        # Status tracking on if reservation is found
        self.complete = False

    def formatted_earlist_time(self):
        return datetime.strptime(self.earlist_time, RESERVATION_TIME_FORMAT)

    def formatted_latest_time(self):
        return datetime.strptime(self.latest_time, RESERVATION_TIME_FORMAT)

    # For logging
    def __repr__(self):
        return json.dumps({'url': self.url, 'size': self.size, 'year': self.year, 'month': self.month, 'days': self.days, 'earlist_time': self.earlist_time, 'latest_time': self.latest_time})

class ReserveTFL():
    def __init__(self, task):
        options = Options()
        if ENABLE_PROXY:
            options.add_argument('--load-extension={}'.format(EXTENSION_PATH))
            options.add_argument('--user-data-dir={}'.format(USER_DATA_DIR))
            options.add_argument('--profile-directory=Default')

        self.driver = webdriver.Chrome(options=options)
        self.task = task

    def teardown(self):
        self.driver.quit()

    def reserve(self):
        print("Looking for availability on month: %s, days: %s, between times: %s and %s" % (self.task.month, self.task.days, self.task.earlist_time, self.task.latest_time))

        if ENABLE_LOGIN:
            self.login_tock()

        while not self.task.complete:
            time.sleep(REFRESH_DELAY_MSEC / 1000)
            self.driver.get("https://www.exploretock.com/%s/search?date=%s-%s-02&size=%s&time=%s" % (self.task.url, self.task.year, month_num(self.task.month), self.task.size, "22%3A00"))
            WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_DELAY_MS).until(expected_conditions.presence_of_element_located((By.CSS_SELECTOR, "div.ConsumerCalendar-month")))

            if not self.search_month():
                print("No available days found. Continuing next search iteration")
                continue

            WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_DELAY_MS).until(expected_conditions.presence_of_element_located((By.CSS_SELECTOR, "button.Consumer-resultsListItem.is-available")))

            print("Found availability. Sleeping for 10 minutes to complete reservation...")
            self.task.complete = True
            time.sleep(BROWSER_CLOSE_DELAY_SEC)

    def login_tock(self):
        self.driver.get("https://www.exploretock.com/tfl/login")
        WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_DELAY_MS).until(expected_conditions.presence_of_element_located((By.NAME, "email")))
        self.driver.find_element(By.NAME, "email").send_keys(TOCK_USERNAME)
        self.driver.find_element(By.NAME, "password").send_keys(TOCK_PASSWORD)
        self.driver.find_element(By.CSS_SELECTOR, ".Button").click()
        WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_DELAY_MS).until(expected_conditions.visibility_of_element_located((By.CSS_SELECTOR, ".MainHeader-accountName")))

    def search_month(self):
        month_object = None

        for month in self.driver.find_elements(By.CSS_SELECTOR, "div.ConsumerCalendar-month"):
            header = month.find_element(By.CSS_SELECTOR, "div.ConsumerCalendar-monthHeading")
            span = header.find_element(By.CSS_SELECTOR, "span.H1")
            print("Encountered month", span.text)

            if self.task.month in span.text:
                month_object = month
                print("Month", self.task.month, "found")
                break

        if month_object is None:
            print("Month", self.task.month, "not found. Ending search")
            return False

        for day in month_object.find_elements(By.CSS_SELECTOR, "button.ConsumerCalendar-day.is-in-month.is-available"):
            span = day.find_element(By.CSS_SELECTOR, "span.B2")
            print("Encountered day: " + span.text)
            if span.text in self.task.days:
                print("Day %s found. Clicking button" % span.text)
                day.click()

                if self.search_time():
                    print("Time found")
                    return True

        return False

    def search_time(self):
        for item in self.driver.find_elements(By.CSS_SELECTOR, "button.Consumer-resultsListItem.is-available"):
            span = item.find_element(By.CSS_SELECTOR, "span.Consumer-resultsListItemTime")
            span2 = span.find_element(By.CSS_SELECTOR, "span")
            print("Encountered time", span2.text)

            available_time = datetime.strptime(span2.text, RESERVATION_TIME_FORMAT)
            if self.task.formatted_earlist_time() <= available_time <= self.task.formatted_latest_time():
                print("Time %s found. Clicking button" % span2.text)
                item.click()
                return True

        return False


def month_num(month):
    # TODO error handling
    return MONTH_NUM[month.lower()]


def run_reservation(task):
    r = ReserveTFL(task)
    r.reserve()
    r.teardown()


def execute_reservations():
    threads = []
    for task in create_tasks():
        t = threading.Thread(target=run_reservation, args=(task,))
        threads.append(t)
        t.start()
        time.sleep(THREAD_DELAY_SEC)

    for t in threads:
        t.join()


def continuous_reservations():
    while True:
        execute_reservations()

# Define tasks, see Task class for defination
def create_tasks():
    tasks = []
    tasks.append(Task("tidal-seattle", "2", "2022", "August", ['23', '24', '25','26', '27', '28', '29'], "5:00 PM", "10:30 PM"))
    tasks.append(Task("taneda", "2", "2022", "September", ['01', '02', '03', '04', '07', '08', '09', '10', '11', '14', '15', '24', '25', '28', '29'], "5:00 PM", "8:30 PM"))
    return tasks

continuous_reservations()
