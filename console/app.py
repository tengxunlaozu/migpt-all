"""Web 控制台

提供浏览器界面：
- 登录小米账号
- 选择设备
- 查看状态/对话记录
- 切换模式
- 控制音量/播放
- 调试日志
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("console")

# 这些在 main.py 中设置
_app_state: dict = {}


def set_app_state(state: dict):
    global _app_state
    _app_state = state


app = FastAPI(title="小爱-Hermes 桥接控制台")

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def api_status():
    """获取当前状态"""
    state = _app_state
    poller = state.get("poller")
    result = {
        "authenticated": bool(state.get("store") and state["store"].is_valid()),
        "device_selected": bool(state.get("device")),
        "polling": poller.running if poller else False,
    }
    if poller:
        result["poller_status"] = poller.get_status()
    if state.get("device"):
        d = state["device"]
        result["device"] = {
            "name": d.name,
            "hardware": d.hardware,
            "device_id": d.device_id,
        }
    return result


@app.post("/api/login")
async def api_login(
    account: str = Form(...),
    password: str = Form(...),
):
    """登录小米账号"""
    state = _app_state
    try:
        auth = state.get("auth")
        if not auth:
            return JSONResponse({"ok": False, "error": "auth client 未初始化"}, status_code=500)

        loop = asyncio.get_event_loop()
        store = await loop.run_in_executor(
            None, lambda: auth.login(account, password)
        )
        state["store"] = store

        # 保存 token
        state["save_token"](store)

        return {"ok": True, "user_id": store.user_id}

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/logout")
async def api_logout():
    """登出"""
    state = _app_state
    state["store"] = None
    state["device"] = None
    return {"ok": True}


@app.get("/api/devices")
async def api_devices():
    """获取设备列表"""
    state = _app_state
    store = state.get("store")
    if not store or not store.is_valid():
        return JSONResponse({"ok": False, "error": "未登录"}, status_code=401)

    try:
        mina = state.get("mina")
        if not mina:
            return JSONResponse({"ok": False, "error": "mina client 未初始化"}, status_code=500)

        loop = asyncio.get_event_loop()
        devices = await loop.run_in_executor(None, lambda: mina.device_list())

        return {
            "ok": True,
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "hardware": d.hardware,
                    "alias": d.alias,
                    "model": d.model,
                    "is_active": d.is_active,
                }
                for d in devices
            ],
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/select_device")
async def api_select_device(request: Request):
    """选择设备"""
    state = _app_state
    body = await request.json()
    device_id = body.get("device_id", "")

    if not device_id:
        return JSONResponse({"ok": False, "error": "device_id 不能为空"}, status_code=400)

    try:
        mina = state.get("mina")
        loop = asyncio.get_event_loop()
        devices = await loop.run_in_executor(None, lambda: mina.device_list())

        selected = None
        for d in devices:
            if d.device_id == device_id:
                selected = d
                break

        if not selected:
            return JSONResponse({"ok": False, "error": f"找不到设备 {device_id}"}, status_code=404)

        state["device"] = selected

        # 保存配置
        state["save_device"](selected)

        return {"ok": True, "device": {"name": selected.name, "hardware": selected.hardware}}

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/start")
async def api_start():
    """启动轮询"""
    state = _app_state
    poller = state.get("poller")
    if not poller:
        return JSONResponse({"ok": False, "error": "poller 未初始化"}, status_code=500)

    if not state.get("device"):
        return JSONResponse({"ok": False, "error": "请先选择设备"}, status_code=400)

    try:
        # 初始化 poller
        device = state["device"]

        # 探测 MIoT DID
        miio = state.get("miio")
        miot_did = device.miot_did
        if not miot_did and miio:
            try:
                loop = asyncio.get_event_loop()
                devices = await loop.run_in_executor(None, lambda: miio.device_list_full())
                for md in devices:
                    if md.model and md.model in (device.model or ""):
                        miot_did = md.did
                        break
            except Exception:
                pass

        poller.initialize(
            auth=state["auth"],
            mina=state["mina"],
            miio=state["miio"],
            hermes=state["hermes"],
            device=device,
            miot_did=miot_did,
            hardware=device.hardware,
        )

        loop = asyncio.get_event_loop()
        await poller.start()

        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/stop")
async def api_stop():
    """停止轮询"""
    state = _app_state
    poller = state.get("poller")
    if poller:
        await poller.stop()
    return {"ok": True}


@app.post("/api/mode")
async def api_mode(request: Request):
    """切换模式"""
    body = await request.json()
    mode = body.get("mode", "")
    state = _app_state
    poller = state.get("poller")
    if not poller:
        return JSONResponse({"ok": False, "error": "poller 未初始化"}, status_code=500)

    try:
        await poller.set_mode(mode)
        state["save_config"](state["config"])
        return {"ok": True, "mode": mode}
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/speak")
async def api_speak(request: Request):
    """主动播报"""
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"ok": False, "error": "text 不能为空"}, status_code=400)

    state = _app_state
    poller = state.get("poller")
    if poller:
        await poller.speak(text)
    return {"ok": True}


@app.post("/api/execute")
async def api_execute(request: Request):
    """执行指令"""
    body = await request.json()
    text = body.get("text", "")
    state = _app_state
    poller = state.get("poller")
    if poller:
        await poller.execute_command(text)
    return {"ok": True}


@app.post("/api/volume")
async def api_volume(request: Request):
    """设置音量"""
    body = await request.json()
    volume = body.get("volume", 50)
    state = _app_state
    poller = state.get("poller")
    if poller:
        await poller.set_volume(int(volume))
    return {"ok": True}


@app.post("/api/reset_session")
async def api_reset_session():
    """重置语音会话"""
    state = _app_state
    poller = state.get("poller")
    if poller:
        poller.reset_voice_session()
    return {"ok": True}


@app.get("/api/debug_log")
async def api_debug_log(lines: int = 100):
    """获取调试日志"""
    state = _app_state
    log_path = state.get("config", {}).get("debug_log_path", "")
    if not log_path or not os.path.exists(log_path):
        return {"ok": True, "lines": []}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return {"ok": True, "lines": all_lines[-lines:]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
