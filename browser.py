"""Headless Selenium wrapper. One class, one job: drive Chromium without a visible window."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

SNAPSHOT_JS = """
const els = Array.from(document.querySelectorAll(
  'a, button, input, select, textarea, [role="button"], [role="link"], [onclick]'
));
return els.slice(0, 80).map((el, i) => {
  const r = el.getBoundingClientRect();
  let sel = el.tagName.toLowerCase();
  if (el.id) sel += '#' + el.id;
  else if (el.name) sel += '[name="' + el.name + '"]';
  else if (el.className && typeof el.className === 'string' && el.className.trim())
    sel += '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.');
  else if (el.getAttribute('href')) sel += '[href="' + el.getAttribute('href').slice(0, 60) + '"]';
  else if (el.type) sel += '[type="' + el.type + '"]';
  const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').slice(0, 80).replace(/\\s+/g, ' ').trim();
  return {ref: i, tag: el.tagName.toLowerCase(), text, selector: sel,
          x: Math.round(r.x), y: Math.round(r.y),
          visible: r.width > 0 && r.height > 0};
});
"""


class HeadlessBrowser:
    """Owns a single headless Chromium session."""

    def __init__(
        self,
        headless: bool = True,
        width: int = 1280,
        height: int = 900,
        shots_dir: str | Path = "shots",
        timeout: int = 15,
    ) -> None:
        self.headless = headless
        self.width = width
        self.height = height
        self.shots_dir = Path(shots_dir)
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.driver: webdriver.Chrome | None = None
        self._shot_count = 0

    def start(self, url: str | None = None) -> dict[str, Any]:
        """Launch Chromium (headless by default). Optionally navigate."""
        if self.driver is not None:
            return {"ok": True, "reused": True, "url": self.driver.current_url}
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument(f"--window-size={self.width},{self.height}")
        opts.add_argument("--log-level=3")
        # Never touch the desktop notification daemon: on WSLg each Chrome launch
        # can D-Bus-activate xfce4-notifyd, which pops "Unable to start notification
        # daemon" on the Windows side. Headless browsing needs no notifications.
        opts.add_argument("--disable-notifications")
        # Steering away from automation banners breaks fewer sites than you'd think.
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        env = dict(os.environ)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # keep Chrome off the session bus
        service = Service(log_output=os.devnull, env=env)
        self.driver = webdriver.Chrome(options=opts, service=service)
        self.driver.set_page_load_timeout(self.timeout)
        self.driver.implicitly_wait(3)
        if url:
            return self.goto(url)
        return {"ok": True, "url": None, "headless": self.headless}

    def _require(self) -> webdriver.Chrome:
        if self.driver is None:
            self.start()
            assert self.driver is not None
        return self.driver

    def goto(self, url: str) -> dict[str, Any]:
        """Navigate to a URL (adds https:// if scheme missing)."""
        if not url.startswith(("http://", "https://", "file://")):
            url = "https://" + url
        d = self._require()
        d.get(url)
        return {"ok": True, "url": d.current_url, "title": d.title}

    def snapshot(self) -> dict[str, Any]:
        """URL + title + interactive elements (refs the agent can click/type)."""
        d = self._require()
        elements: list[dict[str, Any]] = d.execute_script(SNAPSHOT_JS)
        return {
            "ok": True,
            "url": d.current_url,
            "title": d.title,
            "viewport": {"width": self.width, "height": self.height},
            "elements": elements,
        }

    def click(self, selector: str) -> dict[str, Any]:
        """Click first element matching a CSS selector."""
        d = self._require()
        el = WebDriverWait(d, self.timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
        )
        el.click()
        return {"ok": True, "clicked": selector, "url": d.current_url}

    def type_text(self, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        """Clear + fill an input/textarea matching a CSS selector."""
        d = self._require()
        el = WebDriverWait(d, self.timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
        )
        el.clear()
        el.send_keys(text)
        if submit:
            el.send_keys(Keys.ENTER)
        return {"ok": True, "typed": selector, "chars": len(text)}

    def press(self, key: str = "ENTER") -> dict[str, Any]:
        """Press a key on the focused element (ENTER, TAB, ESCAPE, ...)."""
        d = self._require()
        el = d.switch_to.active_element
        el.send_keys(getattr(Keys, key.upper(), Keys.ENTER))
        return {"ok": True, "pressed": key, "url": d.current_url}

    def scroll(self, pixels: int = 800) -> dict[str, Any]:
        """Scroll vertically by pixels (negative = up)."""
        d = self._require()
        d.execute_script("window.scrollBy(0, arguments[0]);", pixels)
        y: int = d.execute_script("return window.scrollY;")
        return {"ok": True, "scrollY": y}

    def back(self) -> dict[str, Any]:
        d = self._require()
        d.back()
        return {"ok": True, "url": d.current_url}

    def forward(self) -> dict[str, Any]:
        d = self._require()
        d.forward()
        return {"ok": True, "url": d.current_url}

    def reload(self) -> dict[str, Any]:
        d = self._require()
        d.refresh()
        return {"ok": True, "url": d.current_url}

    def dom(self, limit: int = 8000) -> dict[str, Any]:
        """Full HTML, truncated to `limit` chars."""
        d = self._require()
        html: str = d.execute_script("return document.documentElement.outerHTML;")
        return {"ok": True, "url": d.current_url, "length": len(html), "html": html[:limit]}

    def assert_text(self, text: str) -> dict[str, Any]:
        """Check visible text contains `text`."""
        d = self._require()
        body = d.find_element(By.TAG_NAME, "body").text
        return {"ok": text in body, "found": text in body, "needle": text}

    def screenshot(self, name: str | None = None) -> dict[str, Any]:
        """Capture viewport PNG into shots dir. Returns path (agent reads this file)."""
        d = self._require()
        self._shot_count += 1
        out = Path(name) if name else self.shots_dir / f"shot-{self._shot_count:03d}.png"
        if not out.is_absolute():
            out = self.shots_dir / out.name if out.parent == Path(".") else out
        out.parent.mkdir(parents=True, exist_ok=True)
        d.save_screenshot(str(out))
        raw = base64.b64encode(out.read_bytes()).decode() if out.exists() else ""
        return {"ok": True, "path": str(out), "png_base64": raw[:200] + "…(truncated, read file)" if raw else ""}

    def quit(self) -> dict[str, Any]:
        if self.driver is not None:
            try:
                self.driver.quit()
            finally:
                self.driver = None
        return {"ok": True, "closed": True}
