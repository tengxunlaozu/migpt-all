#!/usr/bin/env python3
"""
小爱-Hermes 桥接服务

把小爱音箱接进 Hermes Agent，实现：
- 语音对话轮询 → 转发到 Hermes LLM → TTS 回播
- 唤醒词/代理/静默三种模式
- Web 控制台
- 设备控制（音量、播放、指令执行）

用法:
  python main.py                    # 启动服务
  python main.py --config config.yaml  # 指定配置
  python main.py --console-only     # 只启动控制台（不轮询）
"""

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager

import yaml
import uvicorn

# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from xiaomi.models import BridgeConfig, XiaomiTokenStore, MinaDeviceInfo
from xiaomi.auth import XiaomiAuthClient
from xiaomi.mina import MiNAClient
from xiaomi.miio import MiIOClient
from bridge.hermes_client import HermesClient
from bridge.poller import VoicePoller
from console.app import app, set_app_state


# ─── 日志配置 ───

def setup_logging(config: BridgeConfig, verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler(sys.stdout)]

    if config.debug_log_enabled and config.debug_log_path:
        os.makedirs(os.path.dirname(config.debug_log_path), exist_ok=True)
        fh = logging.FileHandler(config.debug_log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        handlers.append(fh)

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ─── 配置加载 ───

def default_state_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".xiaomi-hermes-bridge")


def load_config(config_path: str | None = None) -> BridgeConfig:
    state_dir = default_state_dir()
    os.makedirs(state_dir, exist_ok=True)

    config = BridgeConfig()
    config.token_store_path = os.path.join(state_dir, "tokens.json")
    config.state_store_path = os.path.join(state_dir, "state.json")
    config.debug_log_path = os.path.join(state_dir, "debug.log")

    path = config_path or os.path.join(state_dir, "config.yaml")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            if hasattr(config, k) and v is not None:
                setattr(config, k, v)

    env_map = {
        "XIAOMI_ACCOUNT": "account",
        "XIAOMI_PASSWORD": "password",
        "HERMES_API_URL": "hermes_api_url",
        "HERMES_API_KEY": "hermes_api_key",
        "HERMES_MODEL": "hermes_model",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val:
            setattr(config, attr, val)

    return config


def save_config(config: BridgeConfig):
    data = {}
    for k in vars(config):
        if k.startswith("_"):
            continue
        v = getattr(config, k)
        if v is not None and v != "" and v != [] and k not in (
            "token_store_path", "state_store_path", "debug_log_path",
        ):
            data[k] = v

    os.makedirs(os.path.dirname(config.state_store_path), exist_ok=True)
    with open(config.state_store_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def load_token_store(path: str) -> XiaomiTokenStore | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return XiaomiTokenStore.from_dict(data)
    except Exception:
        return None


def save_token_store(store: XiaomiTokenStore, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store.to_dict(), f, indent=2)


def load_device(path: str) -> MinaDeviceInfo | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MinaDeviceInfo.from_dict(data)
    except Exception:
        return None


def save_device(device: MinaDeviceInfo, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(device.raw, f, indent=2)


# ─── 主入口 ───

def main():
    parser = argparse.ArgumentParser(description="小爱-Hermes 桥接服务")
    parser.add_argument("--config", "-c", help="配置文件路径")
    parser.add_argument("--console-only", action="store_true", help="只启动控制台")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--port", "-p", type=int, help="控制台端口")
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)
    if args.port:
        config.console_port = args.port

    setup_logging(config, args.verbose)
    logger = logging.getLogger("main")

    logger.info("=" * 50)
    logger.info("  小爱 ↔ Hermes 桥接服务")
    logger.info("=" * 50)

    state_dir = default_state_dir()

    # 初始化组件
    auth = XiaomiAuthClient()
    store = load_token_store(config.token_store_path)

    mina = None
    miio = None
    if store and store.is_valid():
        mina = MiNAClient(auth, store)
        miio = MiIOClient(auth, store, config.server_country)
        logger.info(f"已恢复登录态 user_id={store.user_id}")

    hermes = HermesClient(
        api_url=config.hermes_api_url,
        api_key=config.hermes_api_key,
        model=config.hermes_model,
        system_prompt=config.hermes_system_prompt,
        max_turns=config.voice_context_max_turns,
        max_chars=config.voice_context_max_chars,
    )

    poller = VoicePoller(config)

    device_path = os.path.join(state_dir, "device.json")
    device = load_device(device_path)

    # 回调：登录成功后保存 token 并重建客户端
    def save_token_callback(s: XiaomiTokenStore):
        nonlocal store, mina, miio
        store = s
        save_token_store(s, config.token_store_path)
        mina = MiNAClient(auth, s)
        miio = MiIOClient(auth, s, config.server_country)

    def save_device_callback(d: MinaDeviceInfo):
        save_device(d, device_path)

    def save_config_callback(c: BridgeConfig):
        save_config(c)

    app_state = {
        "config": config,
        "auth": auth,
        "mina": mina,
        "miio": miio,
        "hermes": hermes,
        "store": store,
        "device": device,
        "poller": poller,
        "save_token": save_token_callback,
        "save_device": save_device_callback,
        "save_config": save_config_callback,
    }
    set_app_state(app_state)

    # 自动启动轮询
    async def auto_start():
        update_app_state()
        if args.console_only:
            logger.info("控制台模式，不自动启动轮询")
            return

        if store and store.is_valid() and device:
            logger.info(f"自动启动轮询: {device.name} ({device.hardware})")
            miot_did = device.miot_did
            if not miot_did and miio:
                try:
                    devices = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: miio.device_list_full()
                    )
                    for md in devices:
                        if md.model and md.model in (device.model or ""):
                            miot_did = md.did
                            break
                except Exception as e:
                    logger.warning(f"MIoT DID 探测失败: {e}")

            poller.initialize(
                auth=auth, mina=mina, miio=miio, hermes=hermes,
                device=device, miot_did=miot_did, hardware=device.hardware,
            )
            await poller.start()
        else:
            logger.info("未登录或未选择设备，请通过控制台操作")

    def update_app_state():
        app_state["mina"] = mina
        app_state["miio"] = miio
        app_state["store"] = store
        app_state["device"] = device

    # FastAPI lifespan
    @asynccontextmanager
    async def lifespan(_app):
        asyncio.create_task(auto_start())
        yield
        logger.info("正在关闭...")
        await poller.stop()
        await hermes.close()
        auth.close()
        if mina:
            mina.close()
        if miio:
            miio.close()

    app.router.lifespan_context = lifespan

    # 优雅关闭
    def signal_handler(sig, frame):
        logger.info(f"收到信号 {sig}，正在关闭...")
        asyncio.get_event_loop().call_soon_threadsafe(asyncio.get_event_loop().stop)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"控制台地址: http://{config.console_host}:{config.console_port}")
    logger.info(f"Hermes API: {config.hermes_api_url}")
    logger.info(f"模型: {config.hermes_model}")
    logger.info(f"语音模式: {config.mode}")
    logger.info(f"唤醒词: {config.wake_word_pattern}")

    uvicorn.run(
        app,
        host=config.console_host,
        port=config.console_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
