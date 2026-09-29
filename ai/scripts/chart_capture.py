"""Capture a chart only after its canvas and HTML overlays have settled."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from websockets.asyncio.client import connect


class _DevTools:
    def __init__(self, socket):
        self.socket = socket
        self.sequence = 0
        self.session_id = None
        self.loaded = False

    async def receive(self):
        message = json.loads(await self.socket.recv())
        if message.get("method") == "Page.loadEventFired" and message.get("sessionId") == self.session_id:
            self.loaded = True
        return message

    async def call(self, method, **params):
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": params}
        if self.session_id:
            request["sessionId"] = self.session_id
        await self.socket.send(json.dumps(request))
        while True:
            message = await self.receive()
            if message.get("id") != self.sequence:
                continue
            if "error" in message:
                raise RuntimeError(f"Chart capture {method}: {message['error'].get('message', 'failed')}")
            result = message.get("result", {})
            if "exceptionDetails" in result:
                detail = result["exceptionDetails"]
                reason = detail.get("exception", {}).get("description") or detail.get("text", "failed")
                raise RuntimeError(f"Chart capture: {reason}")
            return result

    async def evaluate(self, expression):
        result = await self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")


async def _debug_url(profile: Path, process) -> str:
    while process.returncode is None:
        try:
            lines = (profile / "DevToolsActivePort").read_text().splitlines()
            if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/devtools/browser/"):
                return f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Chart capture browser exited before loading (exit {process.returncode})")


async def _stop_browser(process) -> None:
    # Only the isolated capture process group is ours to stop, never the user's browser.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.returncode is None:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        pass
    finally:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def capture_chart_png(browser: str, url: str, width: int, height: int, *, timeout: float) -> bytes:
    profile = tempfile.TemporaryDirectory(prefix="pynereal-chart-capture-")
    process = None
    try:
        async with asyncio.timeout(timeout):
            command = [
                browser, "--headless=new", "--disable-gpu", "--disable-dev-shm-usage",
                "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                f"--user-data-dir={profile.name}", f"--window-size={width},{height}", "about:blank",
            ]
            if sys.platform.startswith("linux"):
                command.insert(2, "--no-sandbox")
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
            endpoint = await _debug_url(Path(profile.name), process)
            async with connect(endpoint, proxy=None, max_size=32 * 1024 * 1024, close_timeout=1) as socket:
                cdp = _DevTools(socket)
                target = await cdp.call("Target.createTarget", url="about:blank")
                attached = await cdp.call("Target.attachToTarget", targetId=target["targetId"], flatten=True)
                cdp.session_id = attached["sessionId"]
                await cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                               deviceScaleFactor=1, mobile=False)
                await cdp.call("Page.enable")
                navigation = await cdp.call("Page.navigate", url=url)
                if navigation.get("errorText"):
                    raise RuntimeError(f"Chart capture navigation: {navigation['errorText']}")
                while not cdp.loaded:
                    await cdp.receive()
                while True:
                    ready = await cdp.evaluate("App.chart.prepareCapture()")
                    if not ready or not ready.get("ready"):
                        raise RuntimeError("Chart capture layout is not ready")
                    screenshot = await cdp.call("Page.captureScreenshot", format="png", fromSurface=True,
                                                captureBeyondViewport=False)
                    after = await cdp.evaluate("App.chart.captureLayoutState()")
                    # Live prices can change the scale between readiness and screenshot composition.
                    if after and after.get("ready") and after.get("signature") == ready.get("signature"):
                        png = base64.b64decode(screenshot["data"], validate=True)
                        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise RuntimeError("Chart capture returned an invalid PNG")
                        return png
    finally:
        try:
            if process is not None:
                await _stop_browser(process)
        finally:
            await asyncio.to_thread(profile.cleanup)
