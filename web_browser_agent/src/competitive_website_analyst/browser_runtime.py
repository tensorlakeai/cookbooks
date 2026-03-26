from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from competitive_website_analyst.models import BrowserMetadata


SANDBOX_BROWSER_SERVER = r'''
import argparse
import base64
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright


def extract_metadata(page):
    return page.evaluate(
        """
        () => {
          const visibleText = (elements) =>
            elements
              .filter((el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
              .map((el) => (el.innerText || el.textContent || '').trim())
              .filter(Boolean);
          const title = document.title || '';
          const metaDescription = document.querySelector('meta[name="description"]')?.content || '';
          const ogImage = document.querySelector('meta[property="og:image"]')?.content || '';
          const h1 = visibleText(Array.from(document.querySelectorAll('h1')))[0] || '';
          const navItems = visibleText(Array.from(document.querySelectorAll('nav a'))).slice(0, 12);
          const ctas = visibleText(
            Array.from(document.querySelectorAll('button, a[role="button"], a'))
          ).slice(0, 20);
          return {
            title,
            meta_description: metaDescription,
            h1_hero_text: h1,
            visible_cta_labels: ctas,
            nav_items: navItems,
            og_image_url: ogImage,
          };
        }
        """
    )


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        tool = payload["tool"]
        args = payload.get("args", {})
        response = self.server.handle_tool(tool, args)
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class BrowserServer(HTTPServer):
    def __init__(self, addr, handler, page, started_at):
        super().__init__(addr, handler)
        self.page = page
        self.started_at = started_at

    def handle_tool(self, tool, args):
        if tool == "screenshot":
            png = self.page.screenshot(type="png")
            return {"image_b64": base64.b64encode(png).decode("ascii")}
        if tool == "click_text":
            label = args["text"]
            self.page.get_by_text(label, exact=False).first.click(timeout=3000)
            return {"ok": True}
        if tool == "click_coords":
            self.page.mouse.click(args["x"], args["y"])
            return {"ok": True}
        if tool == "wait":
            seconds = float(args.get("seconds", 1))
            time.sleep(seconds)
            return {"ok": True}
        if tool == "extract_metadata":
            data = extract_metadata(self.page)
            data["page_load_time_ms"] = int((time.time() - self.started_at) * 1000)
            return data
        if tool == "save_screenshot":
            self.page.screenshot(path=args["path"], type="png", full_page=True)
            return {"ok": True}
        if tool == "shutdown":
            return {"ok": True, "shutdown": True}
        raise ValueError(f"unknown tool: {tool}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        started_at = time.time()
        page.goto(args.url, wait_until="networkidle", timeout=45000)
        server = BrowserServer(("127.0.0.1", args.port), Handler, page, started_at)
        try:
            while True:
                server.handle_request()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
'''


SANDBOX_RPC_CLIENT = r"""
import json
import sys
import urllib.request

tool = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
payload = json.dumps({"tool": tool, "args": args}).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8765",
    data=payload,
    headers={"content-type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=15) as response:
    print(response.read().decode("utf-8"))
"""


@dataclass(slots=True)
class SandboxBrowserTools:
    sandbox: object
    port: int = 8765

    def screenshot(self) -> bytes:
        payload = self._rpc("screenshot")
        return base64.b64decode(payload["image_b64"])

    def click_text(self, text: str) -> dict:
        return self._rpc("click_text", {"text": text})

    def click_coords(self, x: int, y: int) -> dict:
        return self._rpc("click_coords", {"x": x, "y": y})

    def wait(self, seconds: float) -> dict:
        return self._rpc("wait", {"seconds": seconds})

    def extract_metadata(self) -> BrowserMetadata:
        return BrowserMetadata.model_validate(self._rpc("extract_metadata"))

    def save_screenshot(self, path: str) -> dict:
        return self._rpc("save_screenshot", {"path": path})

    def shutdown(self) -> dict:
        return self._rpc("shutdown")

    def _rpc(self, tool: str, args: dict | None = None) -> dict:
        result = self.sandbox.run(
            "python",
            args=["/app/browser_rpc.py", tool, json.dumps(args or {})],
            timeout=20,
        )
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout or f"browser rpc failed for {tool}")
        return json.loads(result.stdout)
