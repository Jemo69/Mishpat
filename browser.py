"""Headless Selenium wrapper. One class, one job: drive Chromium without a visible window."""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, Callable

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

def short_exc(exc: Exception) -> str:
    """One-line error, no stack trace."""
    msg = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"


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
        quiet: bool = False,
        driver_port: int = 0,
    ) -> None:
        self.headless = headless
        self.width = width
        self.height = height
        self.shots_dir = Path(shots_dir)
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.quiet = quiet  # paths only, no base64 noise
        self.driver_port = driver_port  # 0 = random; set to isolate parallel runs
        self.driver: webdriver.Chrome | None = None
        self._shot_count = 0
        self.tag = time.strftime("%m%d-%H%M%S")  # session prefix: shots never collide
        self._last_elements: list[dict[str, Any]] = []

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
        opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})  # feeds console()
        # Steering away from automation banners breaks fewer sites than you'd think.
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        env = dict(os.environ)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # keep Chrome off the session bus
        # --disable-dev-shm-usage (above) + isolated ports: the two classic fixes for
        # headless-Chrome crashes and driver collisions under parallel runs.
        service = Service(log_output=os.devnull, env=env, port=self.driver_port)
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
        """URL + title + interactive elements ([N] refs are re-clickable via click)."""
        d = self._require()
        elements: list[dict[str, Any]] = d.execute_script(SNAPSHOT_JS)
        self._last_elements = elements
        return {
            "ok": True,
            "url": d.current_url,
            "title": d.title,
            "viewport": {"width": self.width, "height": self.height},
            "elements": elements,
        }

    def _retry_once(self, fn: Callable[[], Any]) -> Any:
        """Run fn; on a stale-element race (SPA re-rendered mid-action), retry once."""
        try:
            return fn()
        except StaleElementReferenceException:
            return fn()

    def _follow_new_tab(self, d: webdriver.Chrome, before: list[str]) -> dict[str, Any]:
        """If the click opened a tab (target=_blank), follow it. No-op otherwise."""
        deadline = time.time() + 2.0
        while time.time() < deadline:
            new = [h for h in d.window_handles if h not in before]
            if new:
                d.switch_to.window(new[0])
                return {"followed_new_tab": True, "tabs": len(d.window_handles), "url": d.current_url}
            time.sleep(0.2)
        return {"followed_new_tab": False, "tabs": len(d.window_handles), "url": d.current_url}

    def _resolve_index(self, selector: str) -> str:
        """Accept a snapshot ref like [12]: resolve to its selector. Guaranteed re-clickable."""
        import re as _re

        m = _re.fullmatch(r"\[(\d+)\]", selector.strip())
        if not m:
            return selector
        if not self._last_elements:
            raise LookupError("no snapshot taken yet — run snapshot first, then click [N]")
        i = int(m.group(1))
        if not 0 <= i < len(self._last_elements):
            raise LookupError(f"snapshot ref [{i}] out of range (0..{len(self._last_elements) - 1})")
        return self._last_elements[i]["selector"]

    def click(self, selector: str) -> dict[str, Any]:
        """Click first element matching a CSS selector (or a snapshot [N] ref)."""
        d = self._require()
        selector = self._resolve_index(selector)
        before = list(d.window_handles)

        def _do() -> None:
            el = WebDriverWait(d, self.timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            el.click()

        try:
            self._retry_once(_do)
        except TimeoutException:
            raise LookupError(f"nothing clickable matches: {selector!r}")
        out: dict[str, Any] = {"ok": True, "clicked": selector}
        out.update(self._follow_new_tab(d, before))
        return out

    def click_text(self, text: str) -> dict[str, Any]:
        """Click first visible button/link/tab whose text contains `text`. Follows new tabs."""
        d = self._require()
        before = list(d.window_handles)
        lit = f'"{text}"' if "'" not in text else f"'{text}'"
        xp = (
            f"//*[self::a or self::button or self::div[@role='tab'] or self::span]"
            f"[contains(normalize-space(), {lit})]"
        )

        def _do() -> None:
            els = [e for e in d.find_elements(By.XPATH, xp) if e.is_displayed()]
            if not els:
                raise LookupError(f"no visible control containing text: {text!r}")
            els[0].click()

        self._retry_once(_do)
        out = {"ok": True, "clicked_text": text}
        out.update(self._follow_new_tab(d, before))
        return out

    def type_text(self, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        """Clear + fill a VISIBLE input/textarea. Hidden matches fail loudly (closed dialog?)."""
        d = self._require()
        try:
            el = self._retry_once(
                lambda: WebDriverWait(d, self.timeout).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
                )
            )
        except TimeoutException:
            raise LookupError(
                f"no VISIBLE input matches: {selector!r} (missing, or hidden in a closed dialog)"
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

    def tabs(self) -> dict[str, Any]:
        """List open tabs with URLs. Jump with switch_tab."""
        d = self._require()
        cur = d.current_window_handle
        info: list[dict[str, Any]] = []
        try:
            for i, h in enumerate(d.window_handles):
                d.switch_to.window(h)
                info.append({"index": i, "url": d.current_url, "title": d.title})
        finally:
            d.switch_to.window(cur)
        return {"ok": True, "current": d.window_handles.index(cur), "tabs": info}

    def switch_tab(self, index: int) -> dict[str, Any]:
        """Jump to tab by index (see tabs)."""
        d = self._require()
        handles = d.window_handles
        if not 0 <= index < len(handles):
            raise LookupError(f"tab {index} out of range (0..{len(handles) - 1})")
        d.switch_to.window(handles[index])
        return {"ok": True, "tab": index, "url": d.current_url, "title": d.title}

    def console(self, limit: int = 20) -> dict[str, Any]:
        """Recent browser console entries: JS errors, failed requests. Debug without leaving the CLI."""
        d = self._require()
        try:
            entries = d.get_log("browser")[-limit:]
        except Exception as exc:
            return {"ok": False, "error": f"console log unavailable: {exc}"}
        return {
            "ok": True,
            "entries": [{"level": e.get("level"), "message": e.get("message", "")[:500]} for e in entries],
        }

    def js(self, expression: str) -> dict[str, Any]:
        """Evaluate a JS expression, return JSON-able result. Sets auth, reads state, polls."""
        d = self._require()
        try:
            value = d.execute_script(f"return ({expression})")
        except Exception as exc:
            raise RuntimeError(f"js failed: {short_exc(exc)}")
        try:
            import json as _json

            _json.dumps(value)
            return {"ok": True, "value": value}
        except (TypeError, ValueError):
            return {"ok": True, "value": str(value)}

    def wait_for(self, condition: str, timeout: float = 15) -> dict[str, Any]:
        """Poll a JS boolean until true. A broken expression fails fast (first eval raises)."""
        d = self._require()
        try:
            if d.execute_script(f"return !!({condition})"):
                return {"ok": True, "condition": condition}
        except Exception as exc:
            raise RuntimeError(f"bad wait_for expression {condition!r}: {short_exc(exc)}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if d.execute_script(f"return !!({condition})"):
                    return {"ok": True, "condition": condition}
            except Exception:
                pass  # transient mid-poll (navigation); keep polling
            time.sleep(0.5)
        raise TimeoutError(f"wait_for timed out after {timeout}s: {condition}")

    def wait_idle(self, timeout: float = 20) -> dict[str, Any]:
        """Wait for hydration to settle: document ready + DOM quiet for ~1s.

        Call after goto before click/type on JS-heavy apps — guards against
        native-form-submit-before-hydration (credentials in URL) surprises."""
        d = self._require()
        deadline = time.time() + timeout
        last_count, quiet_since = -1, 0.0
        while time.time() < deadline:
            try:
                ready = d.execute_script("return document.readyState")
                count = len(d.find_elements(By.CSS_SELECTOR, "*"))
            except Exception:
                time.sleep(0.5)
                continue
            now = time.time()
            if ready == "complete" and count == last_count:
                if now - quiet_since >= 1.0:
                    return {"ok": True, "idle_after_s": round(now - (deadline - timeout), 1)}
            else:
                quiet_since = now
            last_count = count
            time.sleep(0.5)
        raise TimeoutError(f"page never idled within {timeout}s")

    def checkpoint(self, path: str | Path) -> dict[str, Any]:
        """Save session state (URL, cookies, local+session storage) to a file.

        Restore it later to skip UI login: `shell --restore ck.json`."""
        d = self._require()
        state = {
            "url": d.current_url,
            "cookies": d.get_cookies(),
            "localStorage": d.execute_script("return Object.assign({}, window.localStorage);"),
            "sessionStorage": d.execute_script("return Object.assign({}, window.sessionStorage);"),
        }
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(__import__("json").dumps(state))
        return {"ok": True, "path": str(out)}

    def restore(self, path: str | Path) -> dict[str, Any]:
        """Restore a checkpoint: cookies + storage back, land logged-in."""
        import json as _json

        d = self._require()
        state = _json.loads(Path(path).read_text())
        d.get(state["url"])  # domain must match before cookies can be set
        d.delete_all_cookies()
        for c in state.get("cookies", []):
            c.pop("sameSite", None)  # chromedriver rejects unrecognized values
            try:
                d.add_cookie(c)
            except Exception:
                pass
        for k, v in (state.get("localStorage") or {}).items():
            d.execute_script("window.localStorage.setItem(arguments[0], arguments[1]);", k, v)
        for k, v in (state.get("sessionStorage") or {}).items():
            d.execute_script("window.sessionStorage.setItem(arguments[0], arguments[1]);", k, v)
        d.get(state["url"])
        return {"ok": True, "url": d.current_url, "title": d.title}

    def screenshot(self, name: str | None = None) -> dict[str, Any]:
        """Capture viewport PNG into shots dir. quiet=True returns path only (parseable stdout)."""
        d = self._require()
        self._shot_count += 1
        out = Path(name) if name else self.shots_dir / f"shot-{self.tag}-{self._shot_count:03d}.png"
        if not out.is_absolute():
            out = self.shots_dir / out.name if out.parent == Path(".") else out
        out.parent.mkdir(parents=True, exist_ok=True)
        d.save_screenshot(str(out))
        result: dict[str, Any] = {"ok": True, "path": str(out)}
        if not self.quiet:
            raw = base64.b64encode(out.read_bytes()).decode() if out.exists() else ""
            result["png_base64"] = raw[:200] + "…(truncated, read file)" if raw else ""
        return result

    def quit(self) -> dict[str, Any]:
        if self.driver is not None:
            try:
                self.driver.quit()
            finally:
                self.driver = None
        return {"ok": True, "closed": True}
