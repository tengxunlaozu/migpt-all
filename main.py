#!/usr/bin/env python3
"""
小爱-LLM 桥接服务

把小爱音箱接进 LLM Agent，实现：
- 语音对话轮询 → 转发到 LLM LLM → TTS 回播
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
import sys
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
from bridge.client import LLMClient
from bridge.poller import VoicePoller
from console.app import app, set_app_state


# ─── 日志配置 ───

def setup_logging(config: BridgeConfig, verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler(sys.stdout)]

    if config.debug_log_enabled and config.debug_log_path:
        log_dir = os.path.dirname(config.debug_log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(config.debug_log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        handlers.append(fh)

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ─── 配置加载 ───

def default_state_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".xiaomi-llm-bridge")


# 当前生效的配置文件路径（load_config 解析，save_config 写回同一文件，
# 保证控制台修改的配置重启后仍然生效）
_config_path: str = ""


def resolve_config_path(config_path: str | None = None, state_dir: str | None = None) -> str:
    state_dir = state_dir or default_state_dir()
    if config_path:
        return config_path

    # 项目目录 config.yaml（README 快速开始创建的文件）优先，
    # 其次是 ~/.xiaomi-llm-bridge/config.yaml，最后兼容旧版控制台保存的 state.json
    candidates = [
        os.path.join(PROJECT_ROOT, "config.yaml"),
        os.path.join(state_dir, "config.yaml"),
        os.path.join(state_dir, "state.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return os.path.join(state_dir, "config.yaml")


def load_config(config_path: str | None = None) -> BridgeConfig:
    state_dir = default_state_dir()
    os.makedirs(state_dir, exist_ok=True)

    config = BridgeConfig()
    config.token_store_path = os.path.join(state_dir, "tokens.json")
    config.state_store_path = os.path.join(state_dir, "state.json")
    config.debug_log_path = os.path.join(state_dir, "debug.log")

    global _config_path
    _config_path = resolve_config_path(config_path, state_dir)

    if config_path and not os.path.exists(config_path):
        logging.getLogger("main").warning(f"指定的配置文件不存在: {config_path}")

    if os.path.exists(_config_path):
        with open(_config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            if hasattr(config, k):
                if v is not None:
                    setattr(config, k, v)
            else:
                logging.getLogger("main").warning(
                    f"配置文件 {_config_path} 中的未知配置项已忽略: {k}"
                )

    env_map = {
        "XIAOMI_ACCOUNT": "account",
        "XIAOMI_PASSWORD": "password",
        "LLM_API_URL": "api_url",
        "LLM_API_KEY": "api_key",
        "LLM_MODEL": "model",
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

    # 写回启动时读取的同一个配置文件，控制台的修改重启后才不会丢
    path = _config_path or config.state_store_path
    target_dir = os.path.dirname(path)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
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


def login_with_config_creds(config: BridgeConfig) -> XiaomiTokenStore | None:
    """方式B：用 config.yaml 中的 xiaomi_user_id/xiaomi_pass_token 换取 serviceToken"""
    from xiaomi.auth_portal import AuthPortal

    input_err = AuthPortal.validate_credentials(
        config.xiaomi_user_id, config.xiaomi_pass_token
    )
    if input_err:
        logging.getLogger("main").warning(
            f"config.yaml 中的 xiaomi_* 凭证格式不对：{input_err}"
        )
        return None

    portal = AuthPortal(default_state_dir())
    try:
        result = portal.full_login(
            config.xiaomi_user_id,
            config.xiaomi_pass_token,
            config.xiaomi_device_id,
        )
    finally:
        portal.close()

    if not result.get("micoapi_service_token"):
        logging.getLogger("main").warning(
            "config.yaml 凭证换取 serviceToken 失败："
            f"{portal.last_error or '未知原因'}"
        )
        return None
    return XiaomiTokenStore(
        user_id=result["user_id"],
        pass_token=result["pass_token"],
        device_id=result["device_id"],
        micoapi_ssecurity=result["micoapi_ssecurity"],
        micoapi_service_token=result["micoapi_service_token"],
        xiaomiio_ssecurity=result["xiaomiio_ssecurity"],
        xiaomiio_service_token=result["xiaomiio_service_token"],
    )


# ─── 主入口 ───

def main():
    parser = argparse.ArgumentParser(description="小爱-LLM 桥接服务")
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
    logger.info("  小爱 ↔ LLM 桥接服务")
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

    # 凭证串位检查：小米 passToken 是 V1: 开头，Atria 的 API Key 是 atr_ 开头，
    # 两个字段填反/填串是实际发生过的错误，早点提醒比等 LLM 401 好
    if config.api_key and config.api_key.strip().upper().startswith("V1:"):
        hint = (
            "（Atria 的 key 是 atr_ 开头，见 api.atria-asi.ai/docs）"
            if "atria-asi.ai" in (config.api_url or "")
            else ""
        )
        logger.warning(f"api_key 以 V1: 开头，看起来是小米 passToken 而不是 LLM API Key{hint}")

    llm = LLMClient(
        api_url=config.api_url,
        api_key=config.api_key,
        model=config.model,
        system_prompt=config.system_prompt,
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
        # 注册 token 刷新回调
        mina._on_token_refresh = save_token_callback
        # 更新 poller 的客户端引用
        poller._mina = mina
        poller._miio = miio
        # 同步到控制台状态，保证控制台和轮询器用的是同一组客户端
        # （否则 token 刷新后两边引用不一致，控制台会拿旧 token 请求）
        app_state["store"] = s
        app_state["mina"] = mina
        app_state["miio"] = miio

    def save_device_callback(d: MinaDeviceInfo):
        save_device(d, device_path)

    def save_config_callback(c: BridgeConfig):
        save_config(c)

    # 注册初始客户端的 token 刷新回调
    if mina:
        mina._on_token_refresh = save_token_callback

    app_state = {
        "config": config,
        "auth": auth,
        "mina": mina,
        "miio": miio,
        "llm": llm,
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

        # 方式B：config.yaml 里的 xiaomi_* 凭证自动登录（见 config.yaml.example）
        if (store is None or not store.is_valid()) and config.xiaomi_user_id and config.xiaomi_pass_token:
            logger.info("检测到 config.yaml 中的 xiaomi_* 凭证，正在换取 serviceToken...")
            try:
                new_store = await asyncio.get_running_loop().run_in_executor(
                    None, login_with_config_creds, config
                )
                if new_store:
                    save_token_callback(new_store)
                    logger.info("config.yaml 凭证登录成功")
                else:
                    logger.warning("config.yaml 凭证登录失败（serviceToken 为空），请改用控制台登录")
            except Exception as e:
                logger.warning(f"config.yaml 凭证登录失败: {e}")
            update_app_state()

        if store and store.is_valid() and device:
            logger.info(f"自动启动轮询: {device.name} ({device.hardware})")
            miot_did = device.miot_did
            if not miot_did and miio:
                try:
                    devices = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: miio.device_list_full()
                    )
                    for md in devices:
                        if device.model and md.model == device.model:
                            miot_did = md.did
                            break
                except Exception as e:
                    logger.warning(f"MIoT DID 探测失败: {e}")

            # initialize 内部会同步请求 MIoT spec（阻塞网络），放到线程池执行
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: poller.initialize(
                    auth=auth, mina=mina, miio=miio, llm=llm,
                    device=device, miot_did=miot_did, hardware=device.hardware,
                ),
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
        # 保存引用，避免 auto_start 任务被执行器中途回收
        startup_task = asyncio.create_task(auto_start())
        yield
        logger.info("正在关闭...")
        if not startup_task.done():
            startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"启动任务异常退出: {e}")
        await poller.stop()
        await llm.close()
        auth.close()
        if mina:
            mina.close()
        if miio:
            miio.close()

    app.router.lifespan_context = lifespan

    logger.info(f"控制台地址: http://{config.console_host}:{config.console_port}")
    logger.info(f"LLM API: {config.api_url}")
    logger.info(f"模型: {config.model}")
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
