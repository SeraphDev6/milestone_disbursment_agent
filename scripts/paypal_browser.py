"""Finishes PayPal sandbox pages in a real browser for scripts/sandbox_walkthrough.py.

Given an approval, onboarding or pay link and a sandbox account's email and password, it signs in and clicks
through until PayPal sends the browser back to the app. PayPal's pages change, so instead of a fixed script it
looks at what is on the page each time: an email box, a password box, or a button that moves forward (never one
that cancels). If it gets stuck it leaves the window open for a person to finish.

Needs the `sandbox` dependency group (`uv run --group sandbox ...`). It drives the installed Google Chrome and
falls back to Playwright's Chromium (`uv run --group sandbox playwright install chromium`).
"""

import contextlib
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

# Buttons that move a PayPal page forward, by their visible text.
FORWARD = re.compile(
    r"^\s*(log ?in|next|continue|review|agree|accept and continue|complete|pay( now)?\s*$|pay \$|submit|connect|"
    r"get started|go back to|return to|not now|got it|done)",
    re.IGNORECASE,
)
# Never press these, even when they also match FORWARD ("Pay with Debit or Credit Card", "Cancel and return").
NEVER = re.compile(
    r"cancel|card|sign ?up|create|forgot|change|different|add a|open an account|pay in \d|credit|split|later|terms",
    re.IGNORECASE,
)
# On checkout pages "Return to <store>" is PayPal's cancel; on onboarding pages the same words mean "done".
LEAVE = re.compile(r"return to|go back", re.IGNORECASE)
CHECKOUT_PATHS = ("/checkoutnow", "/checkout", "/pay/", "/webapps/hermes")
ONBOARDING_PATHS = ("onboarding", "bizsignup", "/partner")
TOGGLE_ROLES = {"checkbox", "radio", "tab", "switch", "option", "menuitemradio"}
EMAIL_BOX = "input[type=email]:visible, input#email:visible, input[name=login_email]:visible"
PASSWORD_BOX = "input[type=password]:visible"
COOKIE_DECLINE = "#bannerDeclineButton:visible, button:has-text('Reject'):visible, button:has-text('Decline'):visible"
BUTTONS = "button:visible, input[type=submit]:visible, a[role=button]:visible"
LINKS = "a[href]:visible"


class PayPalBrowser:
    def __init__(self, headless: bool = False):
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(channel="chrome", headless=headless)
        except PlaywrightError:  # no Google Chrome installed
            self._browser = self._pw.chromium.launch(headless=headless)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()

    def finish(self, url: str, email: str, password: str, back_to: str, timeout: float = 180) -> bool:
        """Open url as this account and work through PayPal until the browser lands back on back_to's host.

        True when it got back. False when it got stuck: the window stays open for a person to finish."""
        context = self._browser.new_context()  # own cookies, so the funder and grantee never share a session
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        app_host = urlsplit(back_to).netloc
        logins = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            landed = urlsplit(page.url)
            if landed.netloc == app_host:
                if "cancel" in landed.path:  # PayPal sent us to the app's cancel page: not finished
                    print(f"   ..   PayPal returned to the cancel page ({landed.path})")
                    return False
                page.wait_for_load_state("domcontentloaded")
                context.close()
                return True
            try:
                acted = self._step(page, email, password, logins)
            except _WrongPassword:
                print(f"   ..   PayPal didn't accept the password for {email}")
                return False
            except PlaywrightError:  # the page navigated mid-step; look again
                acted = None
            if acted == "login":
                logins += 1
            if acted:
                page.wait_for_timeout(1500)
                with contextlib.suppress(PlaywrightError):  # a click that doesn't navigate is fine
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
            else:
                page.wait_for_timeout(1000)
        return False

    @staticmethod
    def _step(page, email: str, password: str, logins: int) -> str | None:
        decline = page.locator(COOKIE_DECLINE)
        if decline.count():
            decline.first.click()
            return "cookies"
        box = page.locator(EMAIL_BOX)
        if box.count() and box.first.is_editable() and box.first.input_value().strip().lower() != email.lower():
            box.first.fill(email)
            return _submit(page, "#btnNext") or "email"
        box = page.locator(PASSWORD_BOX)
        if box.count() and box.first.is_editable():
            if logins >= 2:
                raise _WrongPassword
            box.first.fill(password)
            _submit(page, "#btnLogin")
            return "login"
        for selector in ("#payment-submit-btn:visible", "#consentButton:visible"):
            if page.locator(selector).count() and page.locator(selector).first.is_enabled():
                page.locator(selector).first.click()
                return "forward"
        path = urlsplit(page.url).path.lower()
        checkout = any(p in path for p in CHECKOUT_PATHS)
        onboarding = any(p in path for p in ONBOARDING_PATHS)
        # Buttons first. Plain links only on onboarding pages, where PayPal uses them for forward steps ("Return to
        # Test Store" on the done page is an <a>); on checkout the same link is the cancel.
        for selector in (BUTTONS, LINKS) if onboarding else (BUTTONS,):
            found = page.locator(selector)
            for i in range(found.count()):
                b = found.nth(i)
                if _toggle(b):  # payment-option tiles and radio buttons: choosing, not moving forward
                    continue
                text = (b.inner_text() or b.get_attribute("value") or "").strip()
                if not text or not FORWARD.search(text) or NEVER.search(text) or (checkout and LEAVE.search(text)):
                    continue
                if b.is_enabled():
                    b.click()
                    return "forward"
        return None


def _toggle(element) -> bool:
    if (element.get_attribute("role") or "").lower() in TOGGLE_ROLES:
        return True
    return element.get_attribute("aria-checked") is not None or element.get_attribute("aria-pressed") is not None


class _WrongPassword(Exception):
    pass


def _submit(page, button_id: str) -> str:
    button = page.locator(f"{button_id}:visible")
    if button.count():
        button.first.click()
    else:
        page.keyboard.press("Enter")
    return "submitted"
