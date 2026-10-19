"""浏览器代理登录

解决小米 securityStatus:128 的跨设备验证问题。
用户在浏览器中完成登录，浏览器自然处理所有安全验证，
服务端从 cookies 中提取 token。

流程：
1. 浏览器访问 /auth/start → 跳转到小米登录页
2. 用户在浏览器中完成登录（包括安全验证）
3. 小米回调到 /auth/callback
4. 服务端从回调中提取 passToken/userId
5. 用 passToken 获取 micoapi/xiaomiio serviceToken
"""

from __future__ import annotations
import base64
import hashlib
import json
import logging
import time
from urllib.parse import urlparse, parse_qs, urlencode

import httpx

logger = logging.getLogger("xiaomi.auth_portal")

MOBILE_UA = (
    "Android-7.1.1-1.0.0-ONEPLUS A3010-136-"
    "QNE2NFJD3B5AC9C717A9BF549BE5B3C99E08C10E5"
    "App/xiaomi.smarthome"
)


class AuthPortal:
    """浏览器代理登录"""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self._http = httpx.Client(timeout=20, follow_redirects=True)
        # 最近一次失败的真实原因（小米返回的 code/description 等），
        # 供控制台/启动日志展示，避免只报"请检查 passToken 是否正确"这种猜测
        self.last_error: str = ""

    def close(self):
        self._http.close()

    @staticmethod
    def validate_credentials(user_id: str, pass_token: str) -> str:
        """返回空串表示通过，否则返回具体的输入错误说明"""
        user_id = (user_id or "").strip()
        pass_token = (pass_token or "").strip()
        if not user_id or not pass_token:
            return "userId 和 passToken 都不能为空"
        if not user_id.isdigit():
            return (
                f"userId 必须是纯数字（account.xiaomi.com 的 userId cookie），"
                f"当前值以 {user_id[:6]!r} 开头、长度 {len(user_id)}"
            )
        if not pass_token.upper().startswith("V1:"):
            return (
                "passToken 格式不对：应为 V1: 开头的长字符串"
                f"（当前长度 {len(pass_token)}，开头 {pass_token[:6]!r}）——"
                "通常是从别的地方复制错了，例如把 LLM 的 API Key 粘进来了"
            )
        return ""

    def start_login(self, sid: str = "micoapi") -> dict:
        """
        Step 1: 获取小米登录页 URL
        返回 {login_url, _sign, qs, callback, state_id}
        """
        resp = self._http.get(
            "https://account.xiaomi.com/pass/serviceLogin",
            params={"sid": sid, "_json": "true"},
            headers={"User-Agent": MOBILE_UA},
        )
        text = resp.text
        if text.startswith("&&&START&&&"):
            text = text[11:]
        data = json.loads(text)

        if data.get("code") == 0 and data.get("location"):
            # 已经有 session，直接拿 token
            return {"already_logged_in": True, "data": data}

        login_url = data.get("location", "")
        if not login_url:
            # 构造登录页 URL
            login_url = (
                f"https://account.xiaomi.com/pass/serviceLogin?"
                f"sid={sid}&_json=true"
            )

        return {
            "login_url": login_url,
            "sid": sid,
            "_sign": data.get("_sign", ""),
            "qs": data.get("qs", ""),
            "callback": data.get("callback", ""),
        }

    def extract_tokens_from_cookies(
        self, cookies: dict, sid: str = "micoapi"
    ) -> dict | None:
        """
        从浏览器 cookies 中提取 token。
        浏览器完成小米登录后会带有 passToken/userId 等 cookies。
        """
        user_id = cookies.get("userId", "")
        pass_token = cookies.get("passToken", "")
        device_id = cookies.get("deviceId", "")

        if not user_id or not pass_token:
            logger.warning("cookies 中缺少 userId 或 passToken")
            return None

        logger.info(f"从 cookies 提取到 userId={user_id}")
        return {
            "user_id": user_id,
            "pass_token": pass_token,
            "device_id": device_id,
        }

    def _service_login(self, http: httpx.Client, sid: str, cookies: dict) -> dict:
        """GET serviceLogin，返回解析后的 JSON（去掉 &&&START&&& 前缀）"""
        resp = http.get(
            f"https://account.xiaomi.com/pass/serviceLogin?sid={sid}&_json=true",
            cookies=cookies,
        )
        text = resp.text
        if text.startswith("&&&START&&&"):
            text = text[11:]
        return json.loads(text)

    @staticmethod
    def _client_sign(nonce: str, ssecurity: str) -> str:
        raw = base64.b64encode(
            hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode()).digest()
        ).decode()
        # query 里的 '+' 会被服务端按空格解码，签名立刻失效（实测 STS 直接 401）；
        # '/' 和 '=' 在 query 值里是安全的，不动它们，避免影响本来就能通的写法
        return raw.replace("+", "%2B")

    def _mint_service_token(self, location: str, nonce: str, ssecurity: str) -> tuple[str, int]:
        """GET <location>&clientSign=<sign> 换 serviceToken。

        location 是一次性的：同一 URL 再次请求会 401，所以失败要重新
        serviceLogin 拿新 location，而不是原地重发。
        用独立 client、跟随重定向——不带上一步 serviceLogin 留下的 cookie。
        返回 (service_token, HTTP状态码)。
        """
        sep = "&" if "?" in location else "?"
        url = f"{location}{sep}clientSign={self._client_sign(nonce, ssecurity)}"
        with httpx.Client(
            timeout=20, follow_redirects=True, headers={"User-Agent": MOBILE_UA}
        ) as s:
            r = s.get(url, follow_redirects=True)
            token = ""
            for c in s.cookies.jar:
                if c.name == "serviceToken":
                    token = c.value or ""
                    break
            if not token:
                set_cookie = r.headers.get("set-cookie", "")
                if "serviceToken=" in set_cookie:
                    token = set_cookie.split("serviceToken=")[1].split(";")[0] or ""
            return token, r.status_code

    def get_service_token_via_pass_token(
        self,
        user_id: str,
        pass_token: str,
        device_id: str,
        sid: str = "micoapi",
    ) -> dict | None:
        """
        用 passToken 获取 serviceToken。
        这是关键步骤：用已有的 passToken 重新走 serviceLogin，
        这次会返回 ssecurity + location，然后获取 serviceToken。
        """
        try:
            http = httpx.Client(timeout=20, follow_redirects=True, headers={
                "User-Agent": MOBILE_UA,
            })

            # 带 passToken cookies 访问 serviceLogin（空值不要发，
            # 否则会带上 deviceId= 这种空 cookie 干扰校验）
            cookies = {
                "sdkVersion": "3.9",
                "deviceId": device_id,
                "userId": user_id,
                "passToken": pass_token,
            }
            cookies = {k: v for k, v in cookies.items() if v}
            # 只记 cookie 名和长度，不记值——出问题时能一眼看出是不是漏带了 deviceId
            logger.info(
                f"serviceLogin(sid={sid}) 带的 cookie: "
                + ", ".join(f"{k}({len(v)}位)" for k, v in cookies.items())
            )

            auth = self._service_login(http, sid, cookies)

            if auth.get("code") != 0:
                self.last_error = (
                    f"serviceLogin(sid={sid}) 返回 code={auth.get('code')} "
                    f"{auth.get('description', '')}"
                    "（70016 = 小米不认这对 userId+passToken：已过期/被登出/不是同一账号）"
                )
                logger.warning(self.last_error)
                if auth.get("code") == 70016:
                    # 同一对凭证换个接口再试不会变好，而 auth2 这一发
                    # 正是触发 87001 验证码风控的那一发——直接停手
                    return None
                # 尝试直接 POST serviceLoginAuth2
                return self._try_auth2_fallback(user_id, pass_token, device_id, sid)

            ssecurity = auth.get("ssecurity", "")
            nonce = auth.get("nonce", "")
            location = auth.get("location", "")

            if not ssecurity or not location:
                self.last_error = f"serviceLogin 响应缺少 ssecurity/location（code={auth.get('code')}）"
                logger.warning(self.last_error)
                return self._try_auth2_fallback(user_id, pass_token, device_id, sid)

            # location 是一次性的（同一 URL 再请求直接 401），所以失败时
            # 重新 serviceLogin 换一张新 location 再试，最多两次
            service_token = ""
            last_status = None
            for attempt in (1, 2):
                if attempt > 1:
                    logger.info(f"重取新的 {sid} location 再试（第 {attempt} 次）")
                    auth = self._service_login(http, sid, cookies)
                    if auth.get("code") != 0:
                        self.last_error = (
                            f"重试时 serviceLogin(sid={sid}) 返回 code={auth.get('code')} "
                            f"{auth.get('description', '')}"
                        )
                        logger.warning(self.last_error)
                        break
                    ssecurity = auth.get("ssecurity", "")
                    nonce = auth.get("nonce", "")
                    location = auth.get("location", "")
                    if not ssecurity or not location:
                        break
                service_token, last_status = self._mint_service_token(
                    location, nonce, ssecurity
                )
                if service_token:
                    break
                logger.warning(
                    f"第 {attempt} 次换 {sid} serviceToken 失败："
                    f"STS 返回 HTTP {last_status}、无 Set-Cookie"
                )

            http.close()

            if service_token:
                logger.info(f"获取到 {sid} serviceToken: {service_token[:20]}...")
                self.last_error = ""
                return {
                    "user_id": user_id,
                    "pass_token": pass_token,
                    "device_id": device_id,
                    "ssecurity": ssecurity,
                    "service_token": service_token,
                    "sid": sid,
                }

            self.last_error = (
                f"已拿到 ssecurity 但没换到 {sid} serviceToken：location 接口返回 "
                f"HTTP {last_status}、无 Set-Cookie（已重取 location 重试 2 次）"
            )
            logger.warning(self.last_error)
            return None

        except Exception as e:
            self.last_error = f"换取 serviceToken 异常: {e}"
            logger.error(self.last_error)
            return None

    def _try_auth2_fallback(
        self, user_id: str, pass_token: str, device_id: str, sid: str
    ) -> dict | None:
        """备用方案：用 passToken 做 serviceLoginAuth2"""
        try:
            http = httpx.Client(timeout=20, follow_redirects=True, headers={
                "User-Agent": MOBILE_UA,
            })

            # 先获取 _sign
            ck = {
                "userId": user_id,
                "passToken": pass_token,
                "deviceId": device_id,
            }
            ck = {k: v for k, v in ck.items() if v}
            resp1 = http.get(
                "https://account.xiaomi.com/pass/serviceLogin",
                params={"sid": sid, "_json": "true"},
                cookies=ck,
            )
            t1 = resp1.text
            if t1.startswith("&&&START&&&"):
                t1 = t1[11:]
            p1 = json.loads(t1)

            # POST serviceLoginAuth2
            resp2 = http.post(
                "https://account.xiaomi.com/pass/serviceLoginAuth2",
                data={
                    "user": user_id,
                    "_json": "true",
                    "sid": sid,
                    "_sign": p1.get("_sign", ""),
                    "callback": p1.get("callback", ""),
                    "qs": p1.get("qs", ""),
                    "_parset": "true",
                    # 不传 hash，用 passToken cookie 认证
                },
                cookies=ck,
            )
            t2 = resp2.text
            if t2.startswith("&&&START&&&"):
                t2 = t2[11:]
            result = json.loads(t2)

            ssecurity = result.get("ssecurity", "")
            location = result.get("location", "")

            if not ssecurity or not location:
                detail = f"auth2 fallback 也失败: code={result.get('code')} {result.get('description','')}"
                self.last_error = f"{self.last_error}；{detail}" if self.last_error else detail
                logger.warning(detail)
                return None

            # 获取 serviceToken（同一个 helper：签名里 '+' 要转义、独立 client、跟随重定向）
            service_token, status = self._mint_service_token(
                location, result.get("nonce", ""), ssecurity
            )
            http.close()
            if service_token:
                self.last_error = ""
                return {
                    "user_id": user_id,
                    "pass_token": pass_token,
                    "device_id": device_id,
                    "ssecurity": ssecurity,
                    "service_token": service_token,
                    "sid": sid,
                }
            if not self.last_error:
                self.last_error = (
                    f"auth2 fallback 换到了 ssecurity，但 STS 返回 HTTP {status}、无 serviceToken"
                )
            return None

        except Exception as e:
            detail = f"auth2 fallback 异常: {e}"
            self.last_error = f"{self.last_error}；{detail}" if self.last_error else detail
            logger.error(detail)
            return None

    def full_login(self, user_id: str, pass_token: str, device_id: str) -> dict:
        """
        完整登录：获取 micoapi 和 xiaomiio 的 serviceToken
        """
        self.last_error = ""
        micoapi = self.get_service_token_via_pass_token(
            user_id, pass_token, device_id, "micoapi"
        )
        # micoapi 才是控制台判定成败的那一个：它的失败原因不能被
        # 后面 xiaomiio 的尝试覆盖掉
        micoapi_error = self.last_error
        self.last_error = ""
        xiaomiio = self.get_service_token_via_pass_token(
            user_id, pass_token, device_id, "xiaomiio"
        )
        if micoapi is None and micoapi_error:
            self.last_error = micoapi_error
        elif micoapi is not None:
            self.last_error = ""

        return {
            "user_id": user_id,
            "pass_token": pass_token,
            "device_id": device_id,
            "micoapi_ssecurity": micoapi["ssecurity"] if micoapi else "",
            "micoapi_service_token": micoapi["service_token"] if micoapi else "",
            "xiaomiio_ssecurity": xiaomiio["ssecurity"] if xiaomiio else "",
            "xiaomiio_service_token": xiaomiio["service_token"] if xiaomiio else "",
        }
