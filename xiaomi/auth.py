"""小米账号登录认证

基于 xiaomi-client.ts 的登录流程，用 Python 实现：
1. serviceLogin 获取登录参数
2. serviceLoginAuth2 提交账号密码
3. 处理安全验证（手机/邮箱）
4. 获取 micoapi 和 xiaomiio 的 serviceToken
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
import random
import re
import string
import time
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode

import httpx

from .models import XiaomiTokenStore

logger = logging.getLogger("xiaomi.auth")

ACCOUNT_BASE = "https://account.xiaomi.com/pass"
USER_AGENT = (
    "Android-7.1.1-1.0.0-ONEPLUS A3010-136-"
    "QNE2NFJD3B5AC9C717A9BF549BE5B3C99E08C10E5"
    "App/xiaomi.smarthome"
)

# 安全验证相关
VERIFICATION_URL = "https://account.xiaomi.com/pass/identity/authenticate/redirect"
MINA_CONVERSATION_URL = "https://userprofile.mina.mi.com/device_profile/v2/conversation"

XIAOMI_SID_LIST = ["micoapi", "xiaomiio"]


def _hash_password(password: str, sign: str) -> str:
    """对密码进行 SHA1 哈希"""
    return hashlib.sha1(password.encode("utf-8")).hexdigest()


def _random_device_id() -> str:
    """生成随机设备ID"""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=16))


def _generate_nonce() -> str:
    """生成 16 字节 nonce，base64 编码"""
    import base64
    b = bytearray(16)
    ms = int(time.time() * 1000)
    b[0:4] = ms.to_bytes(4, "big")
    b[4:] = os.urandom(12)
    return base64.b64encode(bytes(b)).decode()


class XiaomiAuthError(Exception):
    """登录失败"""
    pass


class XiaomiVerificationRequired(Exception):
    """需要安全验证"""
    def __init__(self, verify_url: str, methods: list, state: dict):
        self.verify_url = verify_url
        self.methods = methods
        self.state = state
        super().__init__(f"需要安全验证: {verify_url}")


class XiaomiAuthClient:
    """小米账号认证客户端"""

    def __init__(self):
        self._http = httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self):
        self._http.close()

    def login(
        self,
        account: str,
        password: str,
        sid: str = "micoapi",
        device_id: str = "",
    ) -> XiaomiTokenStore:
        """
        完整登录流程：
        1. 获取登录参数
        2. 提交凭证
        3. 处理验证
        4. 获取 serviceToken
        """
        if not device_id:
            device_id = _random_device_id()

        store = XiaomiTokenStore(device_id=device_id)

        # Step 1: serviceLogin
        logger.info("Step 1: 获取登录参数...")
        login_params = self._step1_service_login(sid)

        # Step 2: 提交凭证
        logger.info("Step 2: 提交登录凭证...")
        auth_result = self._step2_authenticate(
            account, password, sid, login_params
        )

        # Step 3: 处理回调获取token
        logger.info("Step 3: 获取 serviceToken...")
        self._step3_process_callback(auth_result, sid, store)

        # 获取 xiaomiio token
        if sid != "xiaomiio":
            logger.info("获取 xiaomiio serviceToken...")
            self._fetch_xiaomiio_token(store)

        logger.info(f"登录成功 user_id={store.user_id}")
        return store

    def _step1_service_login(self, sid: str) -> dict:
        """GET serviceLogin 获取登录参数"""
        url = f"{ACCOUNT_BASE}/serviceLogin"
        params = {
            "sid": sid,
            "_json": "true",
        }
        resp = self._http.get(url, params=params)
        text = resp.text
        # 去掉 "&&&START&&&" 前缀
        if text.startswith("&&&START&&&"):
            text = text[len("&&&START&&&"):]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise XiaomiAuthError(f"解析登录参数失败: {text[:200]}")

        if "callback" not in data or "_sign" not in data:
            raise XiaomiAuthError(f"登录参数异常: {data}")

        return data

    def _step2_authenticate(
        self, account: str, password: str, sid: str, params: dict
    ) -> dict:
        """POST serviceLoginAuth2 提交账号密码"""
        url = f"{ACCOUNT_BASE}/serviceLoginAuth2"
        sign = params.get("_sign", "")
        data = {
            "account": account,
            "password": _hash_password(password, sign),
            "sid": sid,
            "_json": "true",
            "callback": params.get("callback", ""),
            "_sign": sign,
            "qs": params.get("qs", ""),
            "_parset": "true",
        }

        resp = self._http.post(url, data=data)
        text = resp.text
        if text.startswith("&&&START&&&"):
            text = text[len("&&&START&&&"):]

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            raise XiaomiAuthError(f"解析认证结果失败: {text[:200]}")

        # 检查是否需要安全验证
        if result.get("identity"):
            identity = result["identity"]
            verify_url = identity.get("url", "")
            methods = identity.get("methods", [])
            raise XiaomiVerificationRequired(
                verify_url=verify_url,
                methods=methods,
                state={"account": account, "password": password, "sid": sid, "params": params},
            )

        if "location" not in result:
            desc = result.get("description", "未知错误")
            code = result.get("code", -1)
            raise XiaomiAuthError(f"登录失败 [{code}]: {desc}")

        return result

    def _step3_process_callback(self, auth_result: dict, sid: str, store: XiaomiTokenStore):
        """处理登录回调，提取 serviceToken"""
        location = auth_result["location"]

        # 从 response 中提取 cookie
        store.user_id = auth_result.get("userId", "")
        store.c_user_id = auth_result.get("cUserId", "")
        store.pass_token = auth_result.get("passToken", "")

        # 跟随 location URL 获取 serviceToken
        resp = self._http.get(location)
        cookies = dict(resp.cookies)

        # 从重定向链中提取 serviceToken 和 ssecurity
        url_parsed = urlparse(str(resp.url))
        qs = parse_qs(url_parsed.query)
        ssecurity = qs.get("ssecurity", [""])[0]
        service_token = qs.get("serviceToken", [""])[0]

        # 也检查 cookies
        if not service_token:
            service_token = cookies.get("serviceToken", "")
        if not ssecurity:
            # ssecurity 可能在 URL fragment 或 body 中
            body = resp.text
            m = re.search(r'"ssecurity"\s*:\s*"([^"]+)"', body)
            if m:
                ssecurity = m.group(1)

        if sid == "micoapi":
            store.micoapi_ssecurity = ssecurity
            store.micoapi_service_token = service_token
        elif sid == "xiaomiio":
            store.xiaomiio_ssecurity = ssecurity
            store.xiaomiio_service_token = service_token

        if not service_token:
            logger.warning(f"未能提取 {sid} 的 serviceToken，尝试从 cookies 获取")
            # 尝试从 cookie jar 获取
            for cookie in self._http.cookies.jar:
                if cookie.name == "serviceToken" and cookie.domain and "xiaomi" in cookie.domain:
                    if sid == "micoapi":
                        store.micoapi_service_token = cookie.value
                    else:
                        store.xiaomiio_service_token = cookie.value
                    break

    def _fetch_xiaomiio_token(self, store: XiaomiTokenStore):
        """获取 xiaomiio 的 serviceToken"""
        try:
            result = self._step1_service_login("xiaomiio")
            auth_result = self._step2_authenticate_by_token(store, "xiaomiio", result)
            self._step3_process_callback(auth_result, "xiaomiio", store)
        except Exception as e:
            logger.warning(f"获取 xiaomiio token 失败: {e}")

    def _step2_authenticate_by_token(
        self, store: XiaomiTokenStore, sid: str, params: dict
    ) -> dict:
        """用已有 passToken 进行认证"""
        url = f"{ACCOUNT_BASE}/serviceLoginAuth2"
        data = {
            "sid": sid,
            "_json": "true",
            "callback": params.get("callback", ""),
            "_sign": params.get("_sign", ""),
            "qs": params.get("qs", ""),
            "_parset": "true",
        }
        cookies = {
            "userId": store.user_id,
            "passToken": store.pass_token,
        }
        resp = self._http.post(url, data=data, cookies=cookies)
        text = resp.text
        if text.startswith("&&&START&&&"):
            text = text[len("&&&START&&&"):]
        return json.loads(text)

    def ensure_token(
        self, store: XiaomiTokenStore, sid: str = "micoapi"
    ) -> XiaomiTokenStore:
        """确保 token 有效，无效则刷新"""
        if sid == "micoapi" and store.micoapi_service_token:
            return store
        if sid == "xiaomiio" and store.xiaomiio_service_token:
            return store

        # 用 passToken 刷新
        if store.pass_token:
            try:
                params = self._step1_service_login(sid)
                auth = self._step2_authenticate_by_token(store, sid, params)
                self._step3_process_callback(auth, sid, store)
                return store
            except Exception as e:
                logger.warning(f"刷新 {sid} token 失败: {e}")

        raise XiaomiAuthError(f"{sid} token 无效且无法刷新")

    def build_mina_cookies(self, store: XiaomiTokenStore, device_id: str) -> dict:
        """构建 MiNA API 的 cookies"""
        cookies = {
            "userId": store.user_id,
            "serviceToken": store.micoapi_service_token,
        }
        if device_id:
            cookies["deviceId"] = device_id
        return cookies

    def build_miio_cookies(self, store: XiaomiTokenStore) -> dict:
        """构建 MiIO API 的 cookies"""
        return {
            "userId": store.user_id,
            "serviceToken": store.xiaomiio_service_token,
            "PassportDeviceId": store.device_id,
        }
