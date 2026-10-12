# -*- coding: utf-8 -*-
"""Kvist browser skill: Chromium driven by Playwright on the skill's own virtual screen.

The runner started Xvfb and i3 before importing this module (``DISPLAY`` is
set) and bridged 127.0.0.1:8899 to the core's network proxy. Every request
Chromium makes goes through that proxy, including ``localhost``: the core
routes it to an exposed server of the task or refuses it.

Playwright's sync API belongs to the thread that started it, while the runner
serves each call on a new thread; one worker thread therefore owns Playwright
and runs every browser operation in order.
"""
from __future__ import annotations

import http.server
import os
import queue
import shutil
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

PROXY = "http://127.0.0.1:8899"
EXCHANGE_OUT = Path("/kvist/exchange/out")
SITE_ROOT = Path("/tmp/kvist-site")
SITE_PORT = 8765
MAX_SITE_FILES = 400
MAX_SITE_BYTES = 30 * 1024 * 1024
MAX_TEXT = 6000
MAX_EVENTS = 300
_CHROMIUM_CANDIDATES = ("/usr/lib/chromium/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser",
                        "/usr/bin/google-chrome")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

_SNAPSHOT_JS = """
(maxItems) => {
  for (const old of document.querySelectorAll('[data-kvist-ref]')) old.removeAttribute('data-kvist-ref');
  const selector = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],' +
    '[role=menuitem],[role=tab],[role=switch],[contenteditable=true],[onclick]';
  const items = [];
  let n = 0;
  for (const el of document.querySelectorAll(selector)) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (box.width === 0 || box.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    n += 1;
    const ref = 'e' + n;
    el.setAttribute('data-kvist-ref', ref);
    const tag = el.tagName.toLowerCase();
    const labelled = el.labels && el.labels.length ? el.labels[0].innerText : '';
    const name = (el.getAttribute('aria-label') || labelled || el.innerText || el.placeholder || el.title ||
                  el.getAttribute('name') || el.getAttribute('alt') || '').trim().replace(/\\s+/g, ' ').slice(0, 100);
    const item = {ref, tag, role: el.getAttribute('role') || el.type || '', name};
    if (el.getAttribute('href')) item.href = el.getAttribute('href').slice(0, 200);
    if (el.disabled) item.disabled = true;
    if (el.checked) item.checked = true;
    if ((tag === 'input' || tag === 'textarea') && el.type !== 'password') item.value = (el.value || '').slice(0, 100);
    if (tag === 'select') item.value = el.value;
    items.push(item);
    if (items.length >= maxItems) break;
  }
  const text = document.body ? document.body.innerText : '';
  return {title: document.title, url: location.href, elements: items, truncated: n > items.length, text};
}
"""


class _Worker:
    """One thread that owns Playwright; ``run`` executes a function there and waits for it."""

    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="kvist-playwright", daemon=True)
        self._thread.start()

    def run(self, function: Callable[[], Any], timeout: float = 110.0) -> Any:
        future: Future = Future()
        self._jobs.put((function, future))
        return future.result(timeout=timeout)

    def _loop(self) -> None:
        while True:
            function, future = self._jobs.get()
            try:
                future.set_result(function())
            except BaseException as error:  # noqa: BLE001 - handed to the waiting call as its error
                future.set_exception(error)


class _Tab:
    def __init__(self, tab_id: str, page: Any) -> None:
        self.id = tab_id
        self.page = page
        self.events: list[dict[str, Any]] = []
        self.seq = 0
        self.last_text = ""

    def add(self, kind: str, **data: Any) -> None:
        self.seq += 1
        self.events.append({"seq": self.seq, "kind": kind, "at": round(time.time(), 3), **data})
        del self.events[:-MAX_EVENTS]


class Browser:
    def __init__(self) -> None:
        self.worker = _Worker()
        self.playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.tabs: dict[str, _Tab] = {}
        self.current = ""
        self.resource_id = ""
        self.allowed_hosts: set[str] = set()
        self.site_server: threading.Thread | None = None

    # --- lifecycle -------------------------------------------------------

    def ensure(self, ctx: Any) -> None:
        if self.browser is not None:
            return
        executable = next((path for path in _CHROMIUM_CANDIDATES if os.path.exists(path)), None)
        if executable is None:
            raise RuntimeError("Chromium is not installed")
        width, height = (int(part) for part in ctx.display.info()["size"].split("x"))

        def launch() -> None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                executable_path=executable,
                headless=False,
                args=[
                    f"--proxy-server={PROXY}",
                    # Loopback goes through the core too (it is where the task's
                    # servers are routed), except the skill's own page server.
                    f"--proxy-bypass-list=<-loopback>;127.0.0.1:{SITE_PORT}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-features=Translate,MediaRouter",
                    "--disable-gpu",
                    f"--window-size={width},{height}",
                    "--window-position=0,0",
                ],
            )
            self.context = self.browser.new_context(no_viewport=True, accept_downloads=True)
            self.context.on("page", self._adopt)

        self.worker.run(launch)
        self.resource_id = ctx.runtime.register_resource("Chromium browser", kind="browser")
        ctx.ui.notify("browser started on its own screen")

    def close(self, ctx: Any) -> dict[str, Any]:
        if self.browser is None:
            return {"closed": False, "note": "the browser was not running"}

        def shutdown() -> None:
            self.context.close()
            self.browser.close()
            self.playwright.stop()

        self.worker.run(shutdown)
        self.browser = self.context = self.playwright = None
        self.tabs.clear()
        self.current = ""
        if self.resource_id:
            ctx.runtime.release_resource(self.resource_id)
            self.resource_id = ""
        return {"closed": True}

    # --- tabs and events -------------------------------------------------

    def _adopt(self, page: Any) -> _Tab:
        for tab in self.tabs.values():
            if tab.page is page:
                return tab
        tab = _Tab(f"t{len(self.tabs) + 1}", page)
        self.tabs[tab.id] = tab
        self.current = tab.id
        page.on("console", lambda message: tab.add("console", level=message.type, text=message.text[:1000]))
        page.on("pageerror", lambda error: tab.add("console", level="error", text=f"uncaught: {str(error)[:1000]}"))
        page.on("requestfailed", lambda request: tab.add(
            "network", url=request.url[:500], method=request.method,
            error=(request.failure or "failed")[:300]))
        page.on("response", lambda response: tab.add(
            "network", url=response.url[:500], status=response.status,
            denied=response.headers.get("x-kvist-denied") == "1") if response.status >= 400 else None)
        page.on("dialog", lambda dialog: (tab.add("dialog", kind=dialog.type, text=dialog.message[:500]),
                                          dialog.dismiss()))
        page.on("download", lambda download: tab.add("download", url=download.url[:500],
                                                     name=download.suggested_filename,
                                                     path=self._keep_download(download)))
        return tab

    def _keep_download(self, download: Any) -> str:
        EXCHANGE_OUT.mkdir(parents=True, exist_ok=True)
        target = EXCHANGE_OUT / f"download-{uuid.uuid4().hex[:6]}-{Path(download.suggested_filename).name}"
        download.save_as(str(target))
        return str(target)

    def tab(self, tab_id: str | None) -> _Tab:
        key = tab_id or self.current
        if key not in self.tabs:
            raise ValueError(f"no tab {key!r}; open a page first" if not self.tabs else
                             f"no tab {key!r}; tabs: {', '.join(self.tabs)}")
        return self.tabs[key]

    def new_tab(self) -> _Tab:
        return self.worker.run(lambda: self._adopt(self.context.new_page()))


BROWSER = Browser()


# --- entry points ---------------------------------------------------------


def describe() -> dict[str, Any]:
    return {"tools": ["open_page", "open_workspace_page", "snapshot", "click", "type", "select", "press",
                      "scroll", "wait_for", "screenshot", "console", "network", "tabs", "upload",
                      "close_browser"]}


def execute(tool_name: str, arguments: dict[str, Any], context: Any) -> Any:
    handler = _TOOLS.get(tool_name)
    if handler is None:
        raise ValueError(f"unknown tool {tool_name}")
    if tool_name != "close_browser":
        BROWSER.ensure(context)
    return handler(arguments, context)


# --- tools ----------------------------------------------------------------


def _open_page(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    url = args["url"].strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be http(s)://host/…; for workspace files use open_workspace_page")
    _allow_host(parts.hostname, ctx)
    tab = BROWSER.new_tab() if args.get("new_tab") or not BROWSER.tabs else BROWSER.tab(args.get("tab"))
    ctx.ui.notify(f"opening {url}")
    response = _navigate(tab, url, args.get("wait_until", "load"))
    result = _snapshot(tab, 120)
    result["tab"] = tab.id
    result["http_status"] = response
    return result


def _allow_host(host: str, ctx: Any) -> None:
    if host in _LOCAL_HOSTS or host.endswith(".localhost") or host in BROWSER.allowed_hosts:
        return
    granted = ctx.permissions.current()
    domains = (granted["granted"].get("network.internet") or {}).get("domains", []) + \
        (granted["for_this_task"].get("network.internet") or {}).get("domains", [])
    if "*" in domains and "network.internet" in granted["for_this_task"] or host in domains:
        BROWSER.allowed_hosts.add(host)
        return
    answer = ctx.permissions.request("network.internet", f"open {host} in the agent's browser",
                                     scope={"domains": [host]})
    if not answer.get("granted"):
        raise PermissionError(f"the person did not allow opening {host} ({answer.get('status')})")
    BROWSER.allowed_hosts.add(host)


def _navigate(tab: _Tab, url: str, wait_until: str) -> int | None:
    def go() -> int | None:
        response = tab.page.goto(url, wait_until=wait_until, timeout=45000)
        return response.status if response is not None else None

    return BROWSER.worker.run(go)


def _open_workspace_page(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    path = args["path"].strip().strip("/")
    entry = path if path.endswith((".html", ".htm")) else f"{path}/index.html".lstrip("/")
    folder = str(Path(entry).parent) if "/" in entry else "."
    copied, skipped, truncated = _sync_folder(ctx, folder)
    _ensure_site_server()
    relative = Path(entry).name if folder == "." else Path(entry).relative_to(folder).as_posix()
    url = f"http://127.0.0.1:{SITE_PORT}/{relative}?v={int(time.time())}"
    tab = BROWSER.new_tab() if args.get("new_tab") or not BROWSER.tabs else BROWSER.tab(None)
    status = _navigate(tab, url, "load")
    result = _snapshot(tab, 120)
    result.update({"tab": tab.id, "http_status": status, "files_copied": copied, "workspace_path": entry})
    if skipped:
        result["files_not_copied"] = skipped
    if truncated:
        result["note"] = "the folder listing was truncated; deeper files were not copied"
    return result


def _sync_folder(ctx: Any, folder: str) -> tuple[int, list[str], bool]:
    from kvist_skill import SkillError

    listing = ctx.files.tree(folder, max_depth=8)
    files = tree_files(str(listing.get("tree") or ""))
    if len(files) > MAX_SITE_FILES:
        raise ValueError(f"{folder} has more than {MAX_SITE_FILES} files; point at a smaller folder")
    shutil.rmtree(SITE_ROOT, ignore_errors=True)
    SITE_ROOT.mkdir(parents=True)
    total = 0
    skipped: list[str] = []
    for relative in files:
        ctx.runtime.check()
        source = f"{folder}/{relative}" if folder != "." else relative
        try:
            exchange_path = ctx.files.read_bytes(source)
        except SkillError as error:
            # Protected files (.env, keys) and unreadable ones stay out of the copy.
            skipped.append(f"{relative}: {error.code}")
            continue
        target = SITE_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(exchange_path, target)
        total += target.stat().st_size
        if total > MAX_SITE_BYTES:
            raise ValueError(f"{folder} is larger than {MAX_SITE_BYTES // (1024 * 1024)} MiB")
    return len(files) - len(skipped), skipped, bool(listing.get("truncated"))


def tree_files(tree: str) -> list[str]:
    """File paths from the core's list_tree drawing ("├── css/", "│   └── a.css"), relative to its root."""
    files: list[str] = []
    stack: list[str] = []
    for line in tree.splitlines():
        if "── " not in line:
            continue
        prefix, _, name = line.rpartition("── ")
        depth = (len(prefix) + 3) // 4 - 1
        del stack[depth:]
        if name.endswith("/"):
            stack.append(name.rstrip("/"))
        elif name:
            files.append("/".join([*stack, name]))
    return files


def _ensure_site_server() -> None:
    if BROWSER.site_server is not None:
        return
    import functools

    handler = functools.partial(_QuietHandler, directory=str(SITE_ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", SITE_PORT), handler)
    BROWSER.site_server = threading.Thread(target=server.serve_forever, name="kvist-site", daemon=True)
    BROWSER.site_server.start()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server signature
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def _snapshot_tool(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    result = _snapshot(tab, int(args.get("max_items") or 150))
    result["tab"] = tab.id
    return result


def _snapshot(tab: _Tab, max_items: int) -> dict[str, Any]:
    data = BROWSER.worker.run(lambda: tab.page.evaluate(_SNAPSHOT_JS, max_items))
    text = data.pop("text", "") or ""
    tab.last_text = text
    data["text"] = text[:MAX_TEXT]
    data["text_truncated"] = len(text) > MAX_TEXT
    data["errors_since_open"] = sum(1 for event in tab.events if event["kind"] == "console" and event["level"] == "error")
    data["untrusted"] = True
    return data


def _locator(tab: _Tab, args: dict[str, Any]) -> Any:
    page = tab.page
    if args.get("ref"):
        return page.locator(f'[data-kvist-ref="{args["ref"]}"]')
    if args.get("selector"):
        return page.locator(args["selector"]).first
    if args.get("label"):
        return page.get_by_label(args["label"]).first
    if args.get("text"):
        return page.get_by_text(args["text"]).first
    raise ValueError("give ref (from snapshot), selector, label or text")


def _act(tab: _Tab, args: dict[str, Any], ctx: Any, action: Callable[[], None], what: str) -> dict[str, Any]:
    before_url = BROWSER.worker.run(lambda: tab.page.url)
    before_text = tab.last_text or BROWSER.worker.run(lambda: tab.page.evaluate("document.body ? document.body.innerText : ''"))
    seq = tab.seq
    ctx.ui.notify(what)

    def perform() -> None:
        action()
        try:
            tab.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001 - a page that does not navigate simply has nothing to wait for
            pass
        tab.page.wait_for_timeout(400)

    BROWSER.worker.run(perform)
    snapshot = _snapshot(tab, 120)
    after_lines = [line.strip() for line in snapshot["text"].splitlines() if line.strip()]
    before_lines = {line.strip() for line in before_text.splitlines()}
    new_text = [line for line in after_lines if line not in before_lines][:12]
    events = [event for event in tab.events if event["seq"] > seq]
    observation: dict[str, Any] = {
        "action": what,
        "url_before": before_url,
        "url": snapshot["url"],
        "navigated": snapshot["url"] != before_url,
        "new_text": new_text,
        "console_errors": [e["text"] for e in events if e["kind"] == "console" and e["level"] == "error"][:10],
        "failed_requests": [e for e in events if e["kind"] == "network"][:10],
        "dialogs": [e for e in events if e["kind"] == "dialog"],
        "page": {"title": snapshot["title"], "elements": snapshot["elements"][:60]},
        "untrusted": True,
    }
    expected = args.get("expect_text")
    if expected:
        observation["expectation"] = expected
        observation["expectation_met"] = expected in snapshot["text"]
    else:
        observation["note"] = ("The action ran; whether it had the intended effect is shown only by the changes "
                               "above. Pass expect_text to check a specific result.")
    return observation


def _click(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    target = _locator(tab, args)
    return _act(tab, args, ctx, lambda: target.click(timeout=15000), f"click {_describe_target(args)}")


def _type(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    target = _locator(tab, args)

    def action() -> None:
        if args.get("clear", True):
            target.fill(args["value"], timeout=15000)
        else:
            target.press_sequentially(args["value"], timeout=15000)
        if args.get("submit"):
            target.press("Enter")

    return _act(tab, args, ctx, action, f"type into {_describe_target(args)}")


def _select(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    target = _locator(tab, args)
    choice = {"value": args["value"]} if args.get("value") else {"label": args.get("label")}
    if not any(choice.values()):
        raise ValueError("give value or label")
    return _act(tab, args, ctx, lambda: target.select_option(**choice, timeout=15000),
                f"select in {_describe_target(args)}")


def _press(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    key = args["key"]
    if args.get("ref"):
        target = _locator(tab, args)
        return _act(tab, args, ctx, lambda: target.press(key, timeout=15000), f"press {key}")
    return _act(tab, args, ctx, lambda: tab.page.keyboard.press(key), f"press {key}")


def _scroll(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))

    def action() -> None:
        if args.get("ref"):
            _locator(tab, args).scroll_into_view_if_needed(timeout=10000)
        elif args.get("to") == "top":
            tab.page.evaluate("window.scrollTo(0, 0)")
        elif args.get("to") == "bottom":
            tab.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            tab.page.mouse.wheel(0, int(args.get("by") or 600))

    BROWSER.worker.run(action)
    position = BROWSER.worker.run(lambda: tab.page.evaluate("[window.scrollY, document.body.scrollHeight]"))
    return {"scroll_y": position[0], "page_height": position[1], "tab": tab.id}


def _wait_for(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    timeout = float(args.get("timeout_seconds") or 10)
    deadline = time.monotonic() + timeout
    while True:
        ctx.runtime.check()
        met = BROWSER.worker.run(lambda: _condition_met(tab, args))
        if met or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    return {"met": met, "waited_seconds": round(timeout - max(0.0, deadline - time.monotonic()), 2),
            "url": BROWSER.worker.run(lambda: tab.page.url)}


def _condition_met(tab: _Tab, args: dict[str, Any]) -> bool:
    page = tab.page
    if args.get("url_contains") and args["url_contains"] not in page.url:
        return False
    if args.get("selector") and page.locator(args["selector"]).count() == 0:
        return False
    if args.get("text"):
        body = page.evaluate("document.body ? document.body.innerText : ''")
        if args["text"] not in body:
            return False
    return bool(args.get("url_contains") or args.get("selector") or args.get("text"))


def _screenshot(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    EXCHANGE_OUT.mkdir(parents=True, exist_ok=True)
    target = EXCHANGE_OUT / f"screenshot-{uuid.uuid4().hex[:8]}.png"

    def shoot() -> None:
        if args.get("ref"):
            _locator(tab, args).screenshot(path=str(target), timeout=15000)
        else:
            tab.page.screenshot(path=str(target), full_page=bool(args.get("full_page")))

    BROWSER.worker.run(shoot)
    url = BROWSER.worker.run(lambda: tab.page.url)
    artifact = ctx.artifacts.save(str(target), name=target.name, kind="screenshot", caption=f"Screenshot of {url}")
    attachment = ctx.images.add(str(target), name=target.name)
    result: dict[str, Any] = {"artifact_id": artifact["id"], "artifact_path": artifact["path"],
                              "attachment_id": attachment["id"], "width": attachment["width"],
                              "height": attachment["height"], "url": url}
    question = args.get("question")
    if question:
        try:
            result["description"] = ctx.images.analyze([attachment["id"]], question)["text"]
        except Exception as error:  # noqa: BLE001 - Vision being unavailable is reported, the screenshot stays
            result["description_error"] = str(error)
    return result


def _console(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    level = args.get("level", "all")
    since = int(args.get("since") or 0)
    rows = [e for e in tab.events if e["kind"] == "console" and e["seq"] > since
            and (level == "all" or e["level"] == level)]
    return {"messages": rows[-100:], "next": tab.seq, "untrusted": True}


def _network(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    since = int(args.get("since") or 0)
    rows = [e for e in tab.events if e["kind"] == "network" and e["seq"] > since]
    return {"problems": rows[-100:], "next": tab.seq,
            "note": "status 403 with denied: true is Kvist's network policy, not the site."}


def _tabs(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    op = args["op"]
    if op == "switch":
        tab = BROWSER.tab(args.get("tab"))
        BROWSER.current = tab.id
        BROWSER.worker.run(tab.page.bring_to_front)
    elif op == "close":
        tab = BROWSER.tab(args.get("tab"))
        BROWSER.worker.run(tab.page.close)
        del BROWSER.tabs[tab.id]
        BROWSER.current = next(iter(BROWSER.tabs), "")
    rows = []
    for tab in BROWSER.tabs.values():
        rows.append({"tab": tab.id, "url": BROWSER.worker.run(lambda t=tab: t.page.url),
                     "title": BROWSER.worker.run(lambda t=tab: t.page.title()), "current": tab.id == BROWSER.current})
    return {"tabs": rows}


def _upload(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    tab = BROWSER.tab(args.get("tab"))
    current = ctx.permissions.current()
    if "images.original" not in current["granted"] and "images.original" not in current["for_this_task"]:
        answer = ctx.permissions.request("images.original", "upload the person's image into a form on the page")
        if not answer.get("granted"):
            raise PermissionError("the person did not allow giving the original image to the browser")
    path = ctx.images.original(args["attachment_id"])
    target = _locator(tab, args)
    return _act(tab, args, ctx, lambda: target.set_input_files(path, timeout=15000), "upload an attachment")


def _close_browser(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return BROWSER.close(ctx)


def _describe_target(args: dict[str, Any]) -> str:
    for key in ("ref", "label", "text", "selector"):
        if args.get(key):
            return f"{key} {args[key]!r}"
    return "the page"


_TOOLS: dict[str, Callable[[dict[str, Any], Any], Any]] = {
    "open_page": _open_page,
    "open_workspace_page": _open_workspace_page,
    "snapshot": _snapshot_tool,
    "click": _click,
    "type": _type,
    "select": _select,
    "press": _press,
    "scroll": _scroll,
    "wait_for": _wait_for,
    "screenshot": _screenshot,
    "console": _console,
    "network": _network,
    "tabs": _tabs,
    "upload": _upload,
    "close_browser": _close_browser,
}
