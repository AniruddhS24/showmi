"""CDP reverse proxy: makes the showmi Chrome extension look like Chrome's
remote-debugging endpoint to browser-use's cdp-use client.

Flow:
  browser-use (cdp-use WebSocket client)
      ↕ JSON-RPC over WS on /devtools/browser/showmi (this process)
  CDPProxy (below) — stubs browser-level Target.* / Browser.* and multiplexes
                     per-session commands to the right tab bridge
      ↕ JSON-RPC over WS on /cdp-bridge?tabId=N (per attached tab)
  extension/background.js — calls chrome.debugger.sendCommand on the tab
      ↕ CDP v1.3
  user's active Chrome tab

A separate /cdp-control WS lets the proxy ask the extension to open/close tabs
in response to Target.createTarget / Target.closeTarget from browser-use.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def _session_id_for(tab_id: int) -> str:
    return f"session_{tab_id}"


def _tab_id_from_session(session_id: str) -> int:
    try:
        return int(session_id.split("_", 1)[1])
    except (IndexError, ValueError):
        return -1


def _target_info(tab_id: int, url: str = "about:blank", title: str = "") -> dict:
    return {
        "targetId": str(tab_id),
        "type": "page",
        "title": title,
        "url": url,
        "attached": True,
        "canAccessOpener": False,
        "browserContextId": "showmi-ctx",
    }


class CDPProxy:
    def __init__(self) -> None:
        self.control_ws: WebSocket | None = None
        self.tab_bridges: dict[int, WebSocket] = {}
        self.root_ws: WebSocket | None = None

        self.tab_info: dict[int, dict] = {}
        self._emitted_attached: set[int] = set()

        self._control_reqid = 0
        self._control_pending: dict[int, asyncio.Future] = {}

    # ── Extension control channel ──

    async def register_control(self, ws: WebSocket) -> None:
        await ws.accept()
        if self.control_ws is not None:
            try:
                await self.control_ws.close()
            except Exception:
                pass
        self.control_ws = ws
        logger.info("extension control WS connected")
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                await self._handle_control_message(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"control WS error: {e}")
        finally:
            if self.control_ws is ws:
                self.control_ws = None
            for fut in self._control_pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("control channel closed"))
            self._control_pending.clear()
            logger.info("extension control WS disconnected")

    async def _handle_control_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "TAB_ATTACHED":
            tab_id = int(msg["tabId"])
            self.tab_info[tab_id] = {
                "url": msg.get("url", "about:blank"),
                "title": msg.get("title", ""),
            }
            if self.root_ws is not None:
                await self._emit_target_created(tab_id)
                await self._emit_target_attached(tab_id)
        elif typ == "TAB_DETACHED":
            tab_id = int(msg["tabId"])
            self.tab_info.pop(tab_id, None)
            if self.root_ws is not None:
                await self._emit_target_destroyed(tab_id)
        elif typ == "TAB_UPDATED":
            tab_id = int(msg["tabId"])
            if tab_id in self.tab_info:
                if "url" in msg:
                    self.tab_info[tab_id]["url"] = msg["url"]
                if "title" in msg:
                    self.tab_info[tab_id]["title"] = msg["title"]
        elif typ in (
            "CREATE_TAB_OK", "CREATE_TAB_ERR",
            "CLOSE_TAB_OK", "CLOSE_TAB_ERR",
            "ENSURE_AGENT_TAB_OK", "ENSURE_AGENT_TAB_ERR",
        ):
            req_id = msg.get("reqId")
            fut = self._control_pending.pop(req_id, None) if req_id is not None else None
            if fut and not fut.done():
                if typ.endswith("_OK"):
                    # CREATE_TAB and ENSURE_AGENT_TAB return tabId; CLOSE_TAB just acks.
                    fut.set_result(msg.get("tabId") if "tabId" in msg else True)
                else:
                    fut.set_exception(RuntimeError(msg.get("error", typ)))
        else:
            logger.debug(f"unhandled control message: {typ}")

    async def _control_request(self, kind: str, payload: dict, timeout: float = 15.0) -> Any:
        if self.control_ws is None:
            raise RuntimeError("extension control channel not connected")
        self._control_reqid += 1
        req_id = self._control_reqid
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._control_pending[req_id] = fut
        try:
            await self.control_ws.send_text(json.dumps({"type": kind, "reqId": req_id, **payload}))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._control_pending.pop(req_id, None)
            raise RuntimeError(f"{kind} timed out after {timeout}s")

    # ── Per-tab bridge ──

    async def register_bridge(self, ws: WebSocket, tab_id: int) -> None:
        await ws.accept()
        existing = self.tab_bridges.get(tab_id)
        if existing is not None:
            try:
                await existing.close()
            except Exception:
                pass
        self.tab_bridges[tab_id] = ws
        logger.info(f"tab bridge connected: tabId={tab_id}")
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                await self._forward_from_bridge(tab_id, msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"bridge WS error for tabId={tab_id}: {e}")
        finally:
            # Bridge is gone. Tell browser-use so it stops trying to route
            # commands to a dead session — otherwise every follow-up step
            # gets "no bridge" errors and the agent spirals.
            if self.tab_bridges.get(tab_id) is ws:
                del self.tab_bridges[tab_id]
            had_announced = tab_id in self._emitted_attached
            self._emitted_attached.discard(tab_id)
            if had_announced:
                await self._emit_target_destroyed(tab_id)
            self.tab_info.pop(tab_id, None)
            logger.info(f"tab bridge disconnected: tabId={tab_id}")

    async def _forward_from_bridge(self, tab_id: int, msg: dict) -> None:
        # Extension sends: {id, result} or {id, error} for responses,
        #                  {method, params} for events.
        # Add sessionId and relay to browser-use.
        if self.root_ws is None:
            return
        out = {**msg, "sessionId": _session_id_for(tab_id)}
        try:
            await self.root_ws.send_text(json.dumps(out))
        except Exception as e:
            logger.warning(f"forward bridge→root failed: {e}")

    # ── browser-use root connection ──

    async def register_root(self, ws: WebSocket) -> None:
        await ws.accept()
        if self.root_ws is not None:
            try:
                await self.root_ws.close()
            except Exception:
                pass
        self.root_ws = ws
        self._emitted_attached.clear()
        logger.info("browser-use root WS connected")
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                await self._handle_root_message(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"root WS error: {e}")
        finally:
            if self.root_ws is ws:
                self.root_ws = None
            self._emitted_attached.clear()
            logger.info("browser-use root WS disconnected")

    async def _handle_root_message(self, msg: dict) -> None:
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {}) or {}
        session_id = msg.get("sessionId")

        if session_id is not None:
            tab_id = _tab_id_from_session(session_id)
            bridge = self.tab_bridges.get(tab_id)
            if bridge is None:
                await self._send_error(msg_id, f"session {session_id} is gone", session_id)
                # Safety net: emit Target.targetDestroyed so browser-use drops
                # this session instead of retrying commands against it forever.
                if tab_id in self._emitted_attached or tab_id in self.tab_info:
                    await self._emit_target_destroyed(tab_id)
                    self.tab_info.pop(tab_id, None)
                return
            try:
                await bridge.send_text(json.dumps({"id": msg_id, "method": method, "params": params}))
            except Exception as e:
                await self._send_error(msg_id, f"bridge send failed: {e}", session_id)
            return

        logger.debug(f"root → {method} id={msg_id} params={params}")
        await self._dispatch_browser_method(msg_id, method, params)

    async def _send_to_root(self, msg: dict) -> None:
        if self.root_ws is None:
            return
        try:
            await self.root_ws.send_text(json.dumps(msg))
        except Exception as e:
            logger.warning(f"send to root failed: {e}")

    async def _send_result(self, msg_id: Any, result: dict, session_id: str | None = None) -> None:
        out: dict[str, Any] = {"id": msg_id, "result": result}
        if session_id is not None:
            out["sessionId"] = session_id
        await self._send_to_root(out)

    async def _send_error(self, msg_id: Any, err_msg: str, session_id: str | None = None) -> None:
        out: dict[str, Any] = {"id": msg_id, "error": {"code": -32000, "message": err_msg}}
        if session_id is not None:
            out["sessionId"] = session_id
        await self._send_to_root(out)

    # ── Browser-level CDP methods ──

    async def _dispatch_browser_method(self, msg_id: Any, method: str, params: dict) -> None:
        handler = getattr(self, f"_m_{method.replace('.', '_')}", None)
        if handler is None:
            logger.info(f"stubbing unknown browser method: {method}")
            await self._send_result(msg_id, {})
            return
        try:
            result = await handler(params)
            await self._send_result(msg_id, result or {})
        except Exception as e:
            logger.exception(f"error handling {method}")
            await self._send_error(msg_id, str(e))

    async def _m_Browser_getVersion(self, params: dict) -> dict:
        return {
            "protocolVersion": "1.3",
            "product": "Chrome/120.0.0.0",
            "revision": "@showmi",
            "userAgent": "Mozilla/5.0 (compatible) Chrome/120.0.0.0 Safari/537.36",
            "jsVersion": "12.0.0",
        }

    async def _m_Browser_setDownloadBehavior(self, params: dict) -> dict:
        return {}

    async def _m_Target_setDiscoverTargets(self, params: dict) -> dict:
        # Emit Target.targetCreated for all current attached tabs.
        for tab_id in list(self.tab_info):
            await self._emit_target_created(tab_id)
        return {}

    async def _m_Target_setAutoAttach(self, params: dict) -> dict:
        if params.get("autoAttach"):
            for tab_id in list(self.tab_info):
                await self._emit_target_attached(tab_id)
        return {}

    async def _m_Target_getTargets(self, params: dict) -> dict:
        return {
            "targetInfos": [_target_info(t, **self.tab_info[t]) for t in self.tab_info],
        }

    async def _m_Target_attachToTarget(self, params: dict) -> dict:
        target_id = str(params.get("targetId"))
        try:
            tab_id = int(target_id)
        except ValueError:
            raise RuntimeError(f"unknown target {target_id}")
        if tab_id not in self.tab_info:
            raise RuntimeError(f"target {target_id} not attached in extension")
        # Chrome emits Target.attachedToTarget as a side effect of attachToTarget
        # when flatten=true. Mirror that so browser-use's session manager
        # picks up the target and registers the session.
        await self._emit_target_attached(tab_id)
        return {"sessionId": _session_id_for(tab_id)}

    async def _m_Target_createTarget(self, params: dict) -> dict:
        url = params.get("url") or "about:blank"
        tab_id = await self._control_request("CREATE_TAB", {"url": url})
        return {"targetId": str(int(tab_id))}

    async def _m_Target_closeTarget(self, params: dict) -> dict:
        target_id = str(params.get("targetId"))
        try:
            tab_id = int(target_id)
        except ValueError:
            return {"success": False}
        await self._control_request("CLOSE_TAB", {"tabId": tab_id})
        return {"success": True}

    async def _m_Target_activateTarget(self, params: dict) -> dict:
        target_id = str(params.get("targetId"))
        try:
            tab_id = int(target_id)
        except ValueError:
            return {}
        if self.control_ws is not None:
            try:
                await self.control_ws.send_text(
                    json.dumps({"type": "ACTIVATE_TAB", "tabId": tab_id})
                )
            except Exception:
                pass
        return {}

    async def _m_Target_getBrowserContexts(self, params: dict) -> dict:
        return {"browserContextIds": ["showmi-ctx"]}

    async def _m_Target_disposeBrowserContext(self, params: dict) -> dict:
        return {}

    async def _m_Target_createBrowserContext(self, params: dict) -> dict:
        return {"browserContextId": "showmi-ctx"}

    # ── Synthetic events ──

    async def _emit_target_created(self, tab_id: int) -> None:
        info = _target_info(tab_id, **self.tab_info.get(tab_id, {}))
        await self._send_to_root({"method": "Target.targetCreated", "params": {"targetInfo": info}})

    async def _emit_target_attached(self, tab_id: int) -> None:
        if tab_id in self._emitted_attached:
            return
        self._emitted_attached.add(tab_id)
        info = _target_info(tab_id, **self.tab_info.get(tab_id, {}))
        await self._send_to_root({
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": _session_id_for(tab_id),
                "targetInfo": info,
                "waitingForDebugger": False,
            },
        })

    async def _emit_target_destroyed(self, tab_id: int) -> None:
        if tab_id in self._emitted_attached:
            await self._send_to_root({
                "method": "Target.detachedFromTarget",
                "params": {
                    "sessionId": _session_id_for(tab_id),
                    "targetId": str(tab_id),
                },
            })
            self._emitted_attached.discard(tab_id)
        await self._send_to_root({
            "method": "Target.targetDestroyed",
            "params": {"targetId": str(tab_id)},
        })

    # ── Helpers ──

    def is_tab_attached(self, tab_id: int) -> bool:
        return tab_id in self.tab_info and tab_id in self.tab_bridges

    def attached_tab_ids(self) -> list[int]:
        return sorted(self.tab_info.keys())

    async def ensure_agent_tab(self, timeout: float = 15.0) -> int:
        """Make sure a Showmi-group tab exists and the debugger is attached
        to it. Reuses any existing agent tab; otherwise asks the extension
        to create one and waits for the round-trip.
        """
        for tab_id in self.tab_info:
            if tab_id in self.tab_bridges:
                return tab_id
        tab_id = await self._control_request("ENSURE_AGENT_TAB", {}, timeout=timeout)
        return int(tab_id)


_proxy = CDPProxy()


def get_proxy() -> CDPProxy:
    return _proxy


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/json/version")
    async def json_version():
        return JSONResponse({
            "Browser": "Showmi/0.1",
            "Protocol-Version": "1.3",
            "User-Agent": "Mozilla/5.0 (compatible) Chrome/120.0.0.0 Safari/537.36",
            "V8-Version": "12.0.0",
            "WebKit-Version": "537.36",
            "webSocketDebuggerUrl": "ws://localhost:8765/devtools/browser/showmi",
        })

    @router.get("/json")
    @router.get("/json/list")
    async def json_list():
        items = []
        for tab_id, info in _proxy.tab_info.items():
            items.append({
                "id": str(tab_id),
                "type": "page",
                "title": info.get("title", ""),
                "url": info.get("url", "about:blank"),
                "webSocketDebuggerUrl": f"ws://localhost:8765/devtools/browser/showmi",
            })
        return JSONResponse(items)

    @router.websocket("/devtools/browser/showmi")
    async def ws_root(ws: WebSocket):
        await _proxy.register_root(ws)

    @router.websocket("/cdp-control")
    async def ws_control(ws: WebSocket):
        await _proxy.register_control(ws)

    @router.websocket("/cdp-bridge")
    async def ws_bridge(ws: WebSocket, tabId: int):
        await _proxy.register_bridge(ws, tabId)

    return router
