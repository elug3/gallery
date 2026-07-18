#!/usr/bin/env python3
"""Minimal Chrome DevTools Protocol helper for Akamai-blocked brand sites."""

from __future__ import annotations

import json
import time
import urllib.request

from websocket import create_connection

CDP = "http://127.0.0.1:9222"


class CdpSession:
    def __init__(self, ws_url: str):
        self.ws = create_connection(ws_url, timeout=90)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 120):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = json.loads(self.ws.recv())
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})
        raise TimeoutError(method)

    def evaluate(self, expression: str, timeout: float = 120):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return (result.get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def new_page() -> dict:
    req = urllib.request.Request(f"{CDP}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def close_page(page_id: str) -> None:
    try:
        urllib.request.urlopen(f"{CDP}/json/close/{page_id}", timeout=15).read()
    except Exception:  # noqa: BLE001
        pass


def fetch_html(url: str, settle: float = 3.0, wait_selector: str | None = None) -> str:
    page = new_page()
    session = CdpSession(page["webSocketDebuggerUrl"])
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Network.enable")
        session.call("Page.navigate", {"url": url})
        deadline = time.time() + 75
        while time.time() < deadline:
            data = json.loads(session.ws.recv())
            if data.get("method") == "Page.loadEventFired":
                break
        time.sleep(settle)
        if wait_selector:
            for _ in range(20):
                ready = session.evaluate(
                    f"!!document.querySelector({json.dumps(wait_selector)})"
                )
                if ready:
                    break
                time.sleep(0.5)
        html = session.evaluate("document.documentElement.outerHTML")
        return html or ""
    finally:
        session.close()
        close_page(page["id"])


def fetch_json_in_page(url: str, expression: str, settle: float = 2.5):
    """Navigate to *url* then evaluate *expression* (should return JSON-serializable)."""
    page = new_page()
    session = CdpSession(page["webSocketDebuggerUrl"])
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Network.enable")
        session.call("Page.navigate", {"url": url})
        deadline = time.time() + 75
        while time.time() < deadline:
            data = json.loads(session.ws.recv())
            if data.get("method") == "Page.loadEventFired":
                break
        time.sleep(settle)
        return session.evaluate(expression)
    finally:
        session.close()
        close_page(page["id"])
