"""Mishpat: headless Selenium CLI + MCP. Agent opens a terminal, drives the web, reads screenshots."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich import print as rprint

from agent import run_agent
from browser import HeadlessBrowser
from steps import ACTIONS, do_step, short_error
from video import stitch_frames

load_dotenv()

app = typer.Typer(help="Headless Selenium for AI agents: shell, screenshots, autonomous tests, MCP.")


def _browser(headless: bool, shots_dir: str, width: int, height: int) -> HeadlessBrowser:
    return HeadlessBrowser(headless=headless, shots_dir=shots_dir, width=width, height=height)


@app.command()
def shell(
    url: str = typer.Option("https://example.com", help="Starting URL"),
    headed: bool = typer.Option(False, help="Show a visible browser (default: headless)"),
    shots_dir: str = typer.Option("shots", help="Where screenshots are saved"),
) -> None:
    """Interactive REPL. The agent (or human) types commands; every step prints a screenshot path.

    Example session:\n
        mishpat> goto https://example.com\n
        mishpat> snapshot\n
        mishpat> screenshot  ( -> shots/shot-003.png, open it to SEE the page )\n
        mishpat> click "a.more-info"\n
        mishpat> assert "IANA"
    """
    b = _browser(headless=not headed, shots_dir=shots_dir, width=1280, height=900)
    rprint(f"[bold green]Mishpat headless shell[/]  headless={not headed}  shots=./{shots_dir}/")
    rprint("[dim]Type `help` for commands. `quit` closes the browser.[/]")
    rprint(b.start(url))
    while True:
        try:
            line = input("mishpat> ").strip()
        except (EOFError, KeyboardInterrupt):
            line = "quit"
        if not line:
            continue
        parts = shlex.split(line)
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("help", "h"):
                rprint("goto <url> | snapshot | click <sel> | click_text <text> | type <sel> <text> | press [KEY] | scroll [px] | wait <secs> | back | forward | reload | tabs | tab <n> | dom [raw] | console | screenshot | assert <text> | quit")
            elif cmd == "goto" and args:
                rprint(b.goto(args[0]))
                rprint(b.screenshot())
            elif cmd == "snapshot":
                snap = b.snapshot()
                rprint(f"[bold]{snap['title']}[/]  {snap['url']}")
                for el in snap["elements"][:30]:
                    rprint(f"  [{el['ref']}] {el['tag']} {el['selector']!r}  {el['text']!r}")
                rprint(f"[dim]{len(snap['elements'])} interactive elements[/]")
            elif cmd == "click" and args:
                rprint(b.click(args[0]))
                rprint(b.screenshot())
            elif cmd == "click_text" and args:
                rprint(b.click_text(" ".join(args)))
                rprint(b.screenshot())
            elif cmd == "type" and len(args) >= 2:
                rprint(b.type_text(args[0], " ".join(args[1:])))
                rprint(b.screenshot())
            elif cmd == "press":
                rprint(b.press(args[0] if args else "ENTER"))
                rprint(b.screenshot())
            elif cmd == "scroll":
                rprint(b.scroll(int(args[0]) if args else 800))
                rprint(b.screenshot())
            elif cmd == "back":
                rprint(b.back()); rprint(b.screenshot())
            elif cmd == "forward":
                rprint(b.forward()); rprint(b.screenshot())
            elif cmd == "reload":
                rprint(b.reload()); rprint(b.screenshot())
            elif cmd == "wait" and args:
                import time as _t
                _t.sleep(float(args[0]))
                rprint(f"waited {args[0]}s"); rprint(b.screenshot())
            elif cmd == "dom":
                limit = 8000
                raw = False
                for a in args:
                    if a.isdigit():
                        limit = int(a)
                    if a == "raw":
                        raw = True
                d = b.dom(limit=limit)
                if raw:
                    print(d["html"])  # plain print: no wrapping, safe to pipe to grep
                else:
                    rprint(f"[dim]{d['length']} chars[/]\n{d['html'][:3000]}")
            elif cmd == "console":
                for e in b.console()["entries"]:
                    rprint(f"[{e['level']}] {e['message'][:300]}")
            elif cmd == "tabs":
                rprint(b.tabs())
            elif cmd == "tab" and args:
                rprint(b.switch_tab(int(args[0])))
                rprint(b.screenshot())
            elif cmd == "screenshot":
                rprint(b.screenshot(args[0] if args else None))
            elif cmd == "assert" and args:
                rprint(b.assert_text(" ".join(args)))
            elif cmd == "url":
                rprint(b.snapshot()["url"])
            elif cmd in ("quit", "exit", "q"):
                rprint(b.quit())
                break
            else:
                rprint(f"[red]Unknown or incomplete command:[/] {line}  (try `help`)")
        except Exception as exc:  # keep the shell alive; the agent retries
            rprint(f"[red]{short_error(exc)}[/]")


@app.command()
def screenshot(
    url: str = typer.Argument(..., help="Page to capture"),
    out: str = typer.Option("shots/shot.png", help="Output PNG path"),
    headed: bool = typer.Option(False, help="Show browser instead of headless"),
    width: int = typer.Option(1280),
    height: int = typer.Option(900),
) -> None:
    """One-shot headless screenshot (no REPL)."""
    b = _browser(headless=not headed, shots_dir=str(Path(out).parent), width=width, height=height)
    b.start(url)
    rprint(b.screenshot(out))
    b.quit()


@app.command()
def run(
    script: str = typer.Argument(..., help="JSON file: [{\"action\": \"goto\"|\"click\"|\"type\"|\"assert\"|\"screenshot\", ...}]"),
    headed: bool = typer.Option(False, help="Show browser instead of headless"),
    shots_dir: str = typer.Option("shots"),
) -> None:
    """Run a repeatable scripted test. Each step auto-screenshots."""
    steps: list[dict] = json.loads(Path(script).read_text())
    b = _browser(headless=not headed, shots_dir=shots_dir, width=1280, height=900)
    b.start()
    results = []
    failed = 0
    for i, s in enumerate(steps, 1):
        a = s.get("action", "")
        try:
            r = do_step(b, s)
            shot = b.screenshot()
            rprint(f"step {i} {a}: {str(r)[:300]}  📸 {shot['path']}")
            results.append({"step": i, "action": a, "result": r, "shot": shot["path"]})
        except Exception as exc:
            failed += 1
            rprint(f"[red]step {i} {a} FAILED: {short_error(exc)}[/]")
            results.append({"step": i, "action": a, "error": short_error(exc)})
    b.quit()
    rprint(f"[bold]{'FAIL' if failed else 'PASS'}[/]  {len(results)-failed}/{len(results)} steps ok")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def agent(
    goal: Annotated[str, typer.Option(help="What to test, e.g. 'log in and add item to cart'")],
    url: Annotated[str, typer.Option(help="Starting URL")],
    max_steps: int = typer.Option(15),
    model: str = typer.Option(None, help="LLM model (default: $OPENAI_MODEL)"),
    headed: bool = typer.Option(False, help="Show browser instead of headless"),
) -> None:
    """Autonomous mode: the LLM dynamically chooses Selenium commands until done."""
    result = run_agent(goal=goal, start_url=url, max_steps=max_steps, model=model, headless=not headed)
    rprint(f"[bold]Verdict:[/] {result['verdict']}  ({result['steps']} steps)")
    for t in result["transcript"]:
        rprint(f"  step {t['step']}: {t['call']} -> {str(t['result'])[:300]}")


@app.command()
def record(
    url: str = typer.Option("https://example.com", help="Starting URL"),
    script: str = typer.Option(None, help="JSON steps file (same format as `run`). No file = auto-scroll tour."),
    out: str = typer.Option("shots/demo.mp4", help="Output MP4 path"),
    headed: bool = typer.Option(False, help="Show browser while recording"),
    fps: int = typer.Option(2, help="Video framerate"),
    shots_dir: str = typer.Option("shots"),
) -> None:
    """Record a test run to MP4 (local ffmpeg, free). Scripted or auto-scroll tour."""
    import json as _json
    b = _browser(headless=not headed, shots_dir=shots_dir, width=1280, height=900)
    b.start(url)
    frames: list[str] = [b.screenshot()["path"]]
    if script:
        steps: list[dict] = _json.loads(Path(script).read_text())
        for s in steps:
            try:
                do_step(b, s)
            except Exception as exc:
                rprint(f"[red]record step {s.get('action')} failed: {short_error(exc)}[/]")
            frames.append(b.screenshot()["path"])
    else:  # auto-scroll tour: 6 scroll steps for a demo clip
        for _ in range(6):
            b.scroll(600)
            frames.append(b.screenshot()["path"])
    b.quit()
    rprint(stitch_frames(frames, out, fps=fps))


@app.command()
def helpful(
    url: str = typer.Option("https://example.com", help="Starting URL"),
    video_out: str = typer.Option("shots/helpful.mp4", help="MP4 to write on quit"),
    shots_dir: str = typer.Option("shots"),
) -> None:
    """Helpful mode: VISIBLE browser + guided REPL + every step recorded to MP4.

    Use this when you want to record a video of the test: drive the visible
    browser, narrate/record screen with OBS if you want voiceover, and a clean
    MP4 of the run is stitched on quit. All local — no paid recording service.
    """
    b = _browser(headless=False, shots_dir=shots_dir, width=1280, height=900)
    rprint("[bold green]Mishpat helpful mode[/]  visible browser, recording every step")
    rprint(f"[dim]Drive the page, then `quit` stitches {video_out}. Commands: goto/click/type/press/scroll/back/reload/snapshot/screenshot/assert/url/quit[/]")
    rprint(b.start(url))
    frames: list[str] = [b.screenshot()["path"]]
    while True:
        try:
            line = input("helpful> ").strip()
        except (EOFError, KeyboardInterrupt):
            line = "quit"
        if not line:
            continue
        parts = shlex.split(line)
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("help", "h"):
                rprint("goto <url> | snapshot | click <sel> | click_text <text> | type <sel> <text> | press [KEY] | scroll [px] | wait <secs> | back | forward | reload | tabs | tab <n> | dom [raw] | console | screenshot | assert <text> | quit")
            elif cmd == "goto" and args:
                rprint(b.goto(args[0])); frames.append(b.screenshot()["path"])
            elif cmd == "snapshot":
                snap = b.snapshot()
                rprint(f"[bold]{snap['title']}[/]  {snap['url']}  ({len(snap['elements'])} elements)")
            elif cmd == "click" and args:
                rprint(b.click(args[0])); frames.append(b.screenshot()["path"])
            elif cmd == "click_text" and args:
                rprint(b.click_text(" ".join(args))); frames.append(b.screenshot()["path"])
            elif cmd == "type" and len(args) >= 2:
                rprint(b.type_text(args[0], " ".join(args[1:]))); frames.append(b.screenshot()["path"])
            elif cmd == "press":
                rprint(b.press(args[0] if args else "ENTER")); frames.append(b.screenshot()["path"])
            elif cmd == "scroll":
                rprint(b.scroll(int(args[0]) if args else 800)); frames.append(b.screenshot()["path"])
            elif cmd == "wait" and args:
                import time as _t
                _t.sleep(float(args[0]))
                rprint(f"waited {args[0]}s"); frames.append(b.screenshot()["path"])
            elif cmd == "back":
                rprint(b.back()); frames.append(b.screenshot()["path"])
            elif cmd == "forward":
                rprint(b.forward()); frames.append(b.screenshot()["path"])
            elif cmd == "reload":
                rprint(b.reload()); frames.append(b.screenshot()["path"])
            elif cmd == "tabs":
                rprint(b.tabs())
            elif cmd == "tab" and args:
                rprint(b.switch_tab(int(args[0]))); frames.append(b.screenshot()["path"])
            elif cmd == "dom":
                d = b.dom()
                if "raw" in args:
                    print(d["html"])
                else:
                    rprint(f"[dim]{d['length']} chars[/]\n{d['html'][:3000]}")
            elif cmd == "console":
                for e in b.console()["entries"]:
                    rprint(f"[{e['level']}] {e['message'][:300]}")
            elif cmd == "screenshot":
                shot = b.screenshot(args[0] if args else None)
                rprint(shot); frames.append(shot["path"])
            elif cmd == "assert" and args:
                r = b.assert_text(" ".join(args))
                rprint(r)
                rprint("[green]confirmed" if r["ok"] else "[red]not found")
            elif cmd == "url":
                rprint(b.snapshot()["url"])
            elif cmd in ("quit", "exit", "q"):
                rprint(b.quit())
                break
            else:
                rprint(f"[red]Unknown:[/] {line}")
        except Exception as exc:
            rprint(f"[red]{short_error(exc)}[/]")
    rprint(stitch_frames(frames, video_out))


@app.command()
def mcp() -> None:
    """Serve headless Selenium as an MCP server (stdio) — browser-use for agents, no visible window."""
    from fastmcp import FastMCP

    server = FastMCP("mishpat-headless")
    browser = HeadlessBrowser(headless=True)

    @server.tool
    def browser_goto(url: str) -> dict:
        """Navigate headless Chromium to a URL."""
        return browser.goto(url)

    @server.tool
    def browser_snapshot() -> dict:
        """Snapshot: URL, title, interactive elements."""
        return browser.snapshot()

    @server.tool
    def browser_click(selector: str) -> dict:
        """Click CSS selector."""
        out = browser.click(selector)
        out["screenshot"] = browser.screenshot()["path"]
        return out

    @server.tool
    def browser_type(selector: str, text: str) -> dict:
        """Fill input/textarea."""
        out = browser.type_text(selector, text)
        out["screenshot"] = browser.screenshot()["path"]
        return out

    @server.tool
    def browser_screenshot() -> dict:
        """Screenshot to shots/ dir. Returns path + base64."""
        return browser.screenshot()

    @server.tool
    def browser_dom() -> dict:
        """Page HTML (truncated)."""
        return browser.dom()

    @server.tool
    def browser_assert(text: str) -> dict:
        """Assert visible text is present."""
        return browser.assert_text(text)

    @server.tool
    def browser_back() -> dict:
        return browser.back()

    @server.tool
    def browser_click_text(text: str) -> dict:
        """Click first visible button/link/tab containing text. Follows _blank tabs."""
        out = browser.click_text(text)
        out["screenshot"] = browser.screenshot()["path"]
        return out

    @server.tool
    def browser_tabs() -> dict:
        """List open tabs with URLs (Preview links open new tabs)."""
        return browser.tabs()

    @server.tool
    def browser_tab(index: int) -> dict:
        """Jump to tab by index."""
        out = browser.switch_tab(index)
        out["screenshot"] = browser.screenshot()["path"]
        return out

    @server.tool
    def browser_console(limit: int = 20) -> dict:
        """Recent browser console entries: JS errors, failed requests."""
        return browser.console(limit=limit)

    browser.start()
    server.run()


if __name__ == "__main__":
    app()
