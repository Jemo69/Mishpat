# Mishpat — headless Selenium CLI for AI agents

No visible browser. The agent opens a terminal, drives Chromium headless,
and reads screenshots to decide the next command.

## Setup (uv default)

```bash
cd /home/jemo/projects/Mishpat
uv sync
cp .env.example .env   # only needed for `agent` mode
```

Requires: Chromium + chromedriver (already at `/usr/bin/chromium`, `/usr/bin/chromedriver`).

## The 4 modes

```bash
# 1. REPL — the flow you asked for: agent opens terminal, screenshots, continues
uv run python main.py shell --url https://example.com
mishpat> snapshot
mishpat> screenshot            # -> shots/shot-001.png (agent reads this file)
mishpat> click "a"
mishpat> type "input[name=q]" "hello"
mishpat> assert "documentation"
mishpat> quit

# 2. One-shot screenshot
uv run python main.py screenshot https://example.com --out shots/shot.png

# 3. Scripted test (repeatable)
uv run python main.py run tests/demo.json

# 4. Autonomous agent — LLM dynamically picks commands, screenshot feeds back each step
uv run python main.py agent --goal "verify docs link works" --url https://example.com --max-steps 10

# 5. Record video (local ffmpeg, free — no paid service)
uv run python main.py record --url https://example.com --script tests/demo.json --out shots/demo.mp4
uv run python main.py record --url https://example.com --out shots/tour.mp4   # auto-scroll tour

# 6. Helpful mode — VISIBLE browser + guided REPL, stitches MP4 on quit (for video recording)
uv run python main.py helpful --url https://example.com --video-out shots/helpful.mp4

# 5. MCP server — "browser-use without opening a browser" for opencode/pi
uv run python main.py mcp
```

Script format (`tests/demo.json`):
```json
[
  {"action": "goto", "url": "https://example.com"},
  {"action": "assert", "text": "documentation"},
  {"action": "click", "selector": "a"}
]
```

## Give the agent browser-use with no visible window

Add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "mishpat-headless": {
      "type": "local",
      "command": ["/home/jemo/.local/bin/uv", "run", "--project", "/home/jemo/projects/Mishpat/", "/home/jemo/projects/Mishpat/main.py", "mcp"]
    }
  }
}
```

Tools exposed: `browser_goto, browser_snapshot, browser_click, browser_type, browser_screenshot, browser_dom, browser_assert, browser_back`.
All run headless (`--headless=new`); screenshots land in `shots/` for the agent to read.

## Where else to get this (public options)

| Option | Headless? | Notes |
|---|---|---|
| This repo (`mishpat`) | ✅ yes | Selenium, you own it |
| `@modelcontextprotocol/server-playwright` / Playwright MCP | ✅ yes (default) | `npx @modelcontextprotocol/server-playwright --headless`; most mature |
| `browser-use` python lib | ✅ yes (`headless=True`) | agent loop built-in, needs `OPENAI_API_KEY` |
| Your existing `browser-use` MCP (`computer-use/browser-use`) | ❌ no | drives real Chrome via extension — headed by design |
| Selenium MCP (various) | ✅ | thinner than Playwright, same idea as here |

You already have the headed half wired up. This adds the headless half.
