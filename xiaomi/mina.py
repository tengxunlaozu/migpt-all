"""MiNA API 客户端

实现小爱音箱的云端 API 调用：
- 设备列表
- 对话轮询
- TTS 播报
- 播放控制
- 音量控制
- 执行指令
- 音乐搜索
"""

from __future__ import annotations
import json
import logging
import random
import string
import time
from typing import Any, Optional

import httpx

from .models import XiaomiTokenStore, MinaDeviceInfo, ConversationRecord
from .auth import XiaomiAuthClient, MINA_CONVERSATION_URL

logger = logging.getLogger("xiaomi.mina")

MINA_BASE = "https://api2.mina.mi.com"

# 401 退避：连续 401 超过此阈值后，标记 passToken 过期
MAX_401_BEFORE_TOKEN_EXPIRED = 5


def _random_request_id() -> str:
    chars = string.ascii_lowercase + string.digits
    return "app_ios_" + "".join(random.choices(chars, k=30))


class MiNAClient:
    """MiNA API 客户端"""

    def __init__(self, auth: XiaomiAuthClient, store: XiaomiTokenStore):
        self._auth = auth
        self._store = store
        self._consecutive_failures = 0
        self._consecutive_401s = 0
        self._token_expired = False  # passToken 过期标记
        self._on_token_refresh = None  # 回调: (store) -> None
        self._http = self._make_client()

    def _make_client(self) -> httpx.Client:
        """创建新的 httpx 客户端（强制 IPv4，避免连接池僵尸连接）"""
        transport = httpx.HTTPTransport(
            local_address="0.0.0.0",
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
        return httpx.Client(timeout=15.0, transport=transport)

    def update_tokens(self, store: XiaomiTokenStore):
        self._store = store
        self._consecutive_401s = 0
        self._token_expired = False

    @property
    def token_expired(self) -> bool:
        """passToken 是否已过期"""
        return self._token_expired

    def _try_refresh_token(self) -> bool:
        """尝试用 passToken 刷新 micoapi serviceToken"""
        if not self._store.pass_token or not self._store.user_id:
            logger.warning("无法刷新: 缺少 passToken 或 userId")
            self._token_expired = True
            return False

        try:
            from .auth_portal import AuthPortal
            import os
            state_dir = os.path.dirname(os.path.expanduser("~/.xiaomi-llm-bridge/tokens.json"))
            portal = AuthPortal(state_dir)
            result = portal.get_service_token_via_pass_token(
                self._store.user_id,
                self._store.pass_token,
                self._store.device_id,
                sid="micoapi",
            )
            portal.close()

            if result and result.get("service_token"):
                self._store.micoapi_service_token = result["service_token"]
                self._store.micoapi_ssecurity = result.get("ssecurity", "")
                self._consecutive_401s = 0
                logger.info("micoapi serviceToken 刷新成功")
                # 通知保存
                if self._on_token_refresh:
                    try:
                        self._on_token_refresh(self._store)
                    except Exception:
                        pass
                return True
            else:
                logger.warning("passToken 已过期 (code=70016)，需要用户重新登录")
                self._token_expired = True
                return False
        except Exception as e:
            logger.error(f"刷新 token 异常: {e}")
            self._token_expired = True
            return False

    def close(self):
        self._http.close()

    def _request(
        self,
        uri: str,
        data: dict | None = None,
        method: str = "GET",
        device_id: str = "",
        timeout: float = 15.0,
        _retry: bool = True,
    ) -> Any:
        """MiNA API 通用请求"""
        request_id = _random_request_id()
        url = f"{MINA_BASE}{uri}"

        cookies = self._auth.build_mina_cookies(self._store, device_id)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        if method == "GET":
            if "?" in url:
                url += f"&requestId={request_id}"
            else:
                url += f"?requestId={request_id}"
            resp = self._http.request(method, url, cookies=cookies, timeout=timeout)
        else:
            payload = dict(data) if data else {}
            payload["requestId"] = request_id
            resp = self._http.request(
                method, url, data=payload, cookies=cookies, headers=headers, timeout=timeout
            )

        if resp.status_code == 201 or resp.status_code == 200:
            self._consecutive_401s = 0
            try:
                return resp.json()
            except Exception:
                return resp.text
        elif resp.status_code == 401 and _retry:
            self._consecutive_401s += 1
            if self._consecutive_401s >= MAX_401_BEFORE_TOKEN_EXPIRED:
                logger.warning(f"连续 {self._consecutive_401s} 次 401，尝试刷新 token...")
                if self._try_refresh_token():
                    # 刷新成功，重试一次
                    return self._request(uri, data, method, device_id, timeout, _retry=False)
                else:
                    raise Exception("passToken 已过期，需要用户重新登录")
            else:
                raise Exception(f"MiNA API 401 (第 {self._consecutive_401s} 次)")
        else:
            logger.error(f"MiNA API {resp.status_code}: {resp.text[:200]}")
            raise Exception(f"MiNA API error {resp.status_code}")

    def device_list(self, master: int = 0) -> list[MinaDeviceInfo]:
        """获取设备列表"""
        result = self._request(f"/admin/v2/device_list?master={master}")
        devices = []
        for d in result.get("data", []):
            devices.append(MinaDeviceInfo.from_dict(d))
        return devices

    def ubus_request(
        self, device_id: str, method: str, path: str, message: dict
    ) -> Any:
        """发送 ubus 请求到设备"""
        return self._request(
            "/remote/ubus",
            data={
                "deviceId": device_id,
                "path": path,
                "method": method,
                "message": json.dumps(message),
            },
            method="POST",
        )

    def text_to_speech(self, device_id: str, text: str) -> Any:
        """TTS 播报文本"""
        logger.info(f"TTS [{device_id}]: {text[:80]}...")
        return self.ubus_request(device_id, "text_to_speech", "mibrain", {"text": text})

    def execute_text_directive(self, device_id: str, text: str, silent: bool = False) -> Any:
        """执行文本指令（像对小爱说话一样）"""
        logger.info(f"执行指令 [{device_id}]: {text[:80]}...")
        message = {"text": text, "save": 0}
        if silent:
            message["silent"] = 1
        return self.ubus_request(device_id, "text_to_speech", "mibrain", message)

    def play_url(self, device_id: str, url: str, type_: int = 1, media: str = "app_ios") -> Any:
        """播放音频 URL"""
        logger.info(f"播放URL [{device_id}]: {url[:80]}...")
        return self.ubus_request(
            device_id, "player_play_url", "mediaplayer",
            {"url": url, "type": type_, "media": media},
        )

    def player_operation(self, device_id: str, action: str, media: str = "app_ios") -> Any:
        """播放器操作 (play/pause/stop)"""
        return self.ubus_request(
            device_id, "player_play_operation", "mediaplayer",
            {"action": action, "media": media},
        )

    def player_pause(self, device_id: str) -> Any:
        return self.player_operation(device_id, "pause")

    def player_play(self, device_id: str) -> Any:
        return self.player_operation(device_id, "play")

    def player_stop(self, device_id: str) -> Any:
        return self.player_operation(device_id, "stop")

    def set_volume(self, device_id: str, volume: int) -> Any:
        """设置音量 (0-100)"""
        logger.info(f"设置音量 [{device_id}]: {volume}")
        return self.ubus_request(
            device_id, "player_set_volume", "mediaplayer",
            {"volume": volume, "media": "app_ios"},
        )

    def get_player_status(self, device_id: str, media: str = "app_ios") -> Any:
        """获取播放器状态"""
        return self.ubus_request(
            device_id, "player_get_play_status", "mediaplayer",
            {"media": media},
        )

    def search_music(self, query: str, count: int = 6) -> Any:
        """搜索音乐"""
        return self._request(
            "/music/search",
            data={
                "query": query,
                "queryType": 1,
                "offset": 0,
                "count": count,
                "timestamp": str(int(time.time() * 1000)),
            },
            method="GET",
        )

    def fetch_conversation(
        self, hardware: str, device_id: str, limit: int = 3
    ) -> ConversationRecord | None:
        """
        轮询最新对话记录。
        这是核心功能 — 检测用户对小爱说了什么。
        """
        try:
            cookies = self._auth.build_mina_cookies(self._store, device_id)
            cookies["deviceId"] = device_id

            params = {
                "source": "dialogu",
                "hardware": hardware,
                "timestamp": str(int(time.time() * 1000)),
                "limit": str(limit),
            }

            resp = self._http.get(
                MINA_CONVERSATION_URL,
                params=params,
                cookies=cookies,
                timeout=10.0,
            )

            if resp.status_code == 401:
                self._consecutive_401s += 1
                if self._consecutive_401s >= MAX_401_BEFORE_TOKEN_EXPIRED:
                    if self._consecutive_401s == MAX_401_BEFORE_TOKEN_EXPIRED:
                        logger.warning(f"连续 {self._consecutive_401s} 次 401，尝试刷新 token...")
                    if self._try_refresh_token():
                        # 刷新成功，下次轮询会用新 token
                        logger.info("token 刷新成功，下次轮询生效")
                    else:
                        logger.error("passToken 已过期！请通过控制台重新登录: /api/save-credentials")
                return None

            if resp.status_code != 200:
                logger.warning(f"对话轮询 HTTP {resp.status_code}")
                return None

            # 成功响应，重置 401 计数
            self._consecutive_401s = 0

            data = resp.json()
            inner = data.get("data", {})
            if isinstance(inner, str):
                # API 返回双重编码的 JSON 字符串，需要二次解析
                try:
                    inner = json.loads(inner)
                except (json.JSONDecodeError, TypeError, ValueError):
                    records = []
                else:
                    records = inner.get("records", []) if isinstance(inner, dict) else []
            else:
                records = inner.get("records", []) if isinstance(inner, dict) else []
            if not records:
                return None

            # 返回最新的一条
            latest = records[0]
            return ConversationRecord.from_dict(latest)

        except Exception as e:
            logger.error(f"对话轮询失败: {e}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                logger.warning(f"连续 {self._consecutive_failures} 次失败，重建 HTTP 连接池")
                try:
                    self._http.close()
                except Exception:
                    pass
                self._http = self._make_client()
                self._consecutive_failures = 0
            return None

        # 成功则重置计数器
        self._consecutive_failures = 0

    def wake_up(self, device_id: str, data: list = None) -> Any:
        """唤醒设备"""
        if data is None:
            data = []
        return self.ubus_request(device_id, "player_play_operation", "mibrain", {
            "action": "play",
            "type": 0,
            "data": data,
            "ti": int(time.time()),
        })
