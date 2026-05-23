"""Web 控制台

提供浏览器界面：
- 手动配置 passToken/userId/deviceId
- 选择设备
- 查看状态/对话记录
- 切换模式
- 控制音量/播放
- 调试日志
- LLM 大模型配置
"""

from __future__ import annotations
import asyncio
import json
import logging
import re
import os
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("console")

_app_state: dict = {}


def set_app_state(state: dict):
    global _app_state
    _app_state = state


app = FastAPI(title="小爱-LLM 桥接控制台")

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/status")
async def api_status():
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


@app.post("/api/save-credentials")
async def api_save_credentials(request: Request):
    """保存 passToken/userId/deviceId 并获取 serviceToken"""
    body = await request.json()
    user_id = body.get("user_id", "").strip()
    pass_token = body.get("pass_token", "").strip()
    device_id = body.get("device_id", "").strip()

    if not user_id or not pass_token:
        return JSONResponse(
            {"ok": False, "error": "user_id 和 pass_token 必填"}, status_code=400
        )

    state = _app_state
    try:
        from xiaomi.auth_portal import AuthPortal
        from xiaomi.models import XiaomiTokenStore

        state_dir = state["config"].token_store_path.rsplit("/", 1)[0]
        portal = AuthPortal(state_dir)
        loop = asyncio.get_event_loop()

        result = await loop.run_in_executor(
            None, lambda: portal.full_login(user_id, pass_token, device_id)
        )
        portal.close()

        if not result.get("micoapi_service_token"):
            return JSONResponse(
                {"ok": False, "error": "获取 micoapi serviceToken 失败，请检查 passToken 是否正确"},
                status_code=400,
            )

        store = XiaomiTokenStore(
            user_id=result["user_id"],
            pass_token=result["pass_token"],
            device_id=result["device_id"],
            micoapi_ssecurity=result["micoapi_ssecurity"],
            micoapi_service_token=result["micoapi_service_token"],
            xiaomiio_ssecurity=result["xiaomiio_ssecurity"],
            xiaomiio_service_token=result["xiaomiio_service_token"],
        )

        state["store"] = store
        state["save_token"](store)

        # 重建 MiNA 客户端
        from xiaomi.mina import MiNAClient
        from xiaomi.miio import MiIOClient
        auth = state["auth"]
        state["mina"] = MiNAClient(auth, store)
        state["miio"] = MiIOClient(auth, store, state["config"].server_country)

        return {"ok": True, "user_id": store.user_id}

    except Exception as e:
        logger.error(f"保存凭证失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/devices")
async def api_devices():
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
        state["save_device"](selected)

        return {"ok": True, "device": {"name": selected.name, "hardware": selected.hardware}}

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/start")
async def api_start():
    state = _app_state
    poller = state.get("poller")
    if not poller:
        return JSONResponse({"ok": False, "error": "poller 未初始化"}, status_code=500)

    if not state.get("device"):
        return JSONResponse({"ok": False, "error": "请先选择设备"}, status_code=400)

    try:
        device = state["device"]
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
            llm=state["llm"],
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
    state = _app_state
    poller = state.get("poller")
    if poller:
        await poller.stop()
    return {"ok": True}


@app.post("/api/mode")
async def api_mode(request: Request):
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
    body = await request.json()
    text = body.get("text", "")
    state = _app_state
    poller = state.get("poller")
    if poller:
        await poller.execute_command(text)
    return {"ok": True}


@app.get("/api/volume")
async def api_get_volume():
    """获取音箱当前音量"""
    state = _app_state
    poller = state.get("poller")
    if not poller:
        return JSONResponse({"ok": False, "error": "poller 未初始化"}, status_code=500)
    try:
        vol = await poller.get_volume()
        return {"ok": True, "volume": vol}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/volume")
async def api_volume(request: Request):
    body = await request.json()
    volume = body.get("volume", None)
    state = _app_state
    poller = state.get("poller")
    if not poller:
        return JSONResponse({"ok": False, "error": "poller 未初始化"}, status_code=500)

    # 如果没传音量，先读取当前音量
    if volume is None:
        try:
            current = await poller.get_volume()
            if current >= 0:
                volume = current
            else:
                return JSONResponse({"ok": False, "error": "无法获取当前音量"}, status_code=500)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    if poller:
        await poller.set_volume(int(volume))
    return {"ok": True, "volume": int(volume)}


@app.post("/api/reset_session")
async def api_reset_session():
    state = _app_state
    poller = state.get("poller")
    if poller:
        poller.reset_voice_session()
    return {"ok": True}


@app.post("/api/logout")
async def api_logout():
    state = _app_state
    state["store"] = None
    state["device"] = None
    return {"ok": True}


@app.get("/api/debug_log")
async def api_debug_log(lines: int = 100):
    state = _app_state
    cfg = state.get("config")
    log_path = getattr(cfg, "debug_log_path", "") if cfg else ""
    if not log_path or not os.path.exists(log_path):
        return {"ok": True, "lines": []}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return {"ok": True, "lines": all_lines[-lines:]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/llm_config")
async def api_llm_config():
    """读取当前 LLM 大模型配置（API key 脱敏）"""
    state = _app_state
    cfg = state.get("config")
    if not cfg:
        return JSONResponse({"ok": False, "error": "配置未加载"}, status_code=500)

    api_key = getattr(cfg, "api_key", "") or ""
    # 脱敏：前4 + *** + 后4
    if len(api_key) > 8:
        masked = api_key[:4] + "***" + api_key[-4:]
    elif api_key:
        masked = api_key[:2] + "***"
    else:
        masked = ""

    # 从 URL 提取厂商域名
    api_url = getattr(cfg, "api_url", "") or ""
    provider = ""
    m = re.search(r"https?://([^/]+)", api_url)
    if m:
        provider = m.group(1)

    return {
        "ok": True,
        "provider": provider,
        "api_url": api_url,
        "model": getattr(cfg, "model", ""),
        "api_key_masked": masked,
        "api_key_full": api_key,
    }


@app.post("/api/llm_config")
async def api_llm_config_update(request: Request):
    """更新 LLM 大模型配置（运行时热更新 + 持久化）"""
    body = await request.json()
    api_url = body.get("api_url", "").strip()
    model = body.get("model", "").strip()
    api_key = body.get("api_key", "").strip()

    state = _app_state
    cfg = state.get("config")
    llm = state.get("llm")
    if not cfg:
        return JSONResponse({"ok": False, "error": "配置未加载"}, status_code=500)

    changed = []
    if api_url and api_url != cfg.api_url:
        cfg.api_url = api_url
        if llm:
            llm.api_url = api_url.rstrip("/")
        changed.append("API地址")
    if model and model != cfg.model:
        cfg.model = model
        if llm:
            llm.model = model
        changed.append("模型")
    if api_key and api_key != cfg.api_key:
        cfg.api_key = api_key
        if llm:
            llm.api_key = api_key
        changed.append("API密钥")

    if not changed:
        return {"ok": True, "message": "未修改", "changed": []}

    # 持久化
    save_fn = state.get("save_config")
    if save_fn:
        save_fn(cfg)

    logger.info(f"LLM 配置已更新: {', '.join(changed)}")
    return {"ok": True, "message": f"已更新: {', '.join(changed)}", "changed": changed}
