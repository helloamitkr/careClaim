#!/usr/bin/env python3
"""Wrap the implementation-map source into a standalone HTML file.

The page is authored as *body content* — no doctype, no <html>, no <head>, no
<body> — because the Claude artifact host injects that scaffold and a CSS reset
at publish time. A file opened from disk gets neither, so this adds both, plus a
theme toggle the host would otherwise provide.

    python scripts/build_map.py SOURCE.html implementation-map.html

Nothing here is generated from the codebase; the source is hand-written. This
only handles the wrapper, so the published artifact and the file in the repo
cannot drift apart in their scaffolding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# What the artifact host supplies and a file:// page does not.
RESET = """    /* The artifact host supplies these; a file opened from disk does not. */
    *, *::before, *::after { box-sizing: border-box; }
    body, dl, dd, figure, h1, h2, h3, p, pre { margin: 0; }
    img, svg { max-width: 100%; display: block; }
    button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
    table { border-collapse: collapse; }
"""

TOGGLE_CSS = """
  /* Standalone only: the artifact host provides its own theme switch. */
  .theme-toggle {
    position: fixed; top: 1rem; right: 1rem; z-index: 10;
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.35rem 0.7rem;
    font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    background: var(--surface); border: 1px solid var(--rule); border-radius: 999px;
  }
  .theme-toggle:hover { color: var(--accent); border-color: var(--accent); }
  .theme-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  @media print { .theme-toggle, nav.rail { display: none; } }
"""

TOGGLE_HTML = """<button class="theme-toggle" id="themeToggle" type="button" aria-label="Switch colour theme">
  <span id="themeLabel">Dark</span>
</button>

"""

SCRIPT = """
<script>
  (function () {
    var root = document.documentElement;
    var btn = document.getElementById("themeToggle");
    var label = document.getElementById("themeLabel");

    function systemPrefersDark() {
      return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    function current() {
      return root.getAttribute("data-theme") || (systemPrefersDark() ? "dark" : "light");
    }
    function render() {
      label.textContent = current() === "dark" ? "Light" : "Dark";
    }
    try {
      var saved = localStorage.getItem("carebridge-map-theme");
      if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
    } catch (e) { /* file:// with storage disabled */ }
    render();

    btn.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("carebridge-map-theme", next); } catch (e) {}
      render();
    });
  })();
</script>
"""


def build(source: Path) -> str:
    body = source.read_text()

    match = re.search(r"<title>(.*?)</title>", body)
    if match is None:
        sys.exit(f"{source}: no <title> — cannot name the document")
    title = match.group(1)
    body = body.replace(f"<title>{title}</title>\n", "", 1).lstrip("\n")

    body = body.replace("<style>\n", "<style>\n" + RESET, 1)

    # Append the toggle rules to the last <style> block, then close <head> after it.
    last_close = body.rindex("</style>")
    body = body[:last_close] + TOGGLE_CSS + body[last_close:]
    after_style = body.rindex("</style>\n") + len("</style>\n")
    body = body[:after_style] + "</head>\n<body>\n" + body[after_style:]

    body = body.replace('<div class="page">', TOGGLE_HTML + '<div class="page">', 1)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<!--
  CareBridge AI — implementation map.

  Standalone: open this file directly in a browser. Everything is inline; there
  are no external requests.

  Built by scripts/build_map.py. Every path and count in here was read from the
  running system, so regenerate rather than let it rot.
-->
{body}
{SCRIPT}</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-4].strip())
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    html = build(source)
    dest.write_text(html)
    print(f"{dest} — {len(html):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
