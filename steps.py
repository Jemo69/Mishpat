"""One action language for shell, helpful, run, record, and the agent loop.

Every command speaks the same verbs so agents never re-learn per mode:
goto snapshot click click_text type press scroll wait back forward reload
dom console tabs tab screenshot assert
"""
from __future__ import annotations

import time
from typing import Any

from browser import HeadlessBrowser

ACTIONS = (
    "goto snapshot click click_text type press scroll wait "
    "back forward reload dom console tabs tab screenshot assert"
)


def short_error(exc: Exception) -> str:
    """One-line error an agent can act on (no stack traces)."""
    msg = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
    hint = ""
    if "StaleElement" in type(exc).__name__:
        hint = " (page re-rendered mid-action — just retry the command)"
    return f"{type(exc).__name__}: {msg}{hint}"


def do_step(b: HeadlessBrowser, s: dict[str, Any]) -> dict[str, Any]:
    """Execute one action dict. Raises LookupError/RuntimeError on failure."""
    a = s.get("action", "")
    if a == "goto":
        return b.goto(s["url"])
    if a == "click":
        return b.click(s["selector"])
    if a == "click_text":
        return b.click_text(s["text"])
    if a == "type":
        return b.type_text(s["selector"], s.get("text", ""))
    if a == "press":
        return b.press(s.get("key", "ENTER"))
    if a == "scroll":
        return b.scroll(int(s.get("pixels", 800)))
    if a == "wait":
        time.sleep(float(s.get("seconds", 2)))
        return {"ok": True, "waited": s.get("seconds", 2)}
    if a == "back":
        return b.back()
    if a == "forward":
        return b.forward()
    if a == "reload":
        return b.reload()
    if a == "dom":
        return b.dom(limit=int(s.get("limit", 8000)))
    if a == "console":
        return b.console(limit=int(s.get("limit", 20)))
    if a == "tabs":
        return b.tabs()
    if a == "tab":
        return b.switch_tab(int(s["index"]))
    if a == "screenshot":
        return b.screenshot(s.get("name"))
    if a == "assert":
        r = b.assert_text(s["text"])
        if not r["ok"]:
            raise AssertionError(f"text not found: {s['text']!r}")
        return r
    if a == "snapshot":
        return b.snapshot()
    raise ValueError(f"unknown action: {a} (valid: {ACTIONS})")
