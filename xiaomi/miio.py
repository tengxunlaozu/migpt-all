"""MiIO API 客户端

实现 MiOT 协议的设备控制：
- 设备列表
- 属性读写
- 动作执行
- MIoT Spec 查询
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import time
from typing import Any, Optional

import httpx

from .models import XiaomiTokenStore, MiioDeviceInfo, SpeakerFeatureMap

logger = logging.getLogger("xiaomi.miio")

MIOT_SPEC_INSTANCES_URL = "http://miot-spec.org/miot-spec-v2/instances?status=all"
MIOT_SPEC_INSTANCE_URL = "http://miot-spec.org/miot-spec-v2/instance?type="


def _sha256_base64(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return base64.b64encode(h.digest()).decode()


# 使用 micloud 的 RC4 签名工具
try:
    from micloud.miutils import (
        gen_nonce as _gen_nonce,
        signed_nonce as _signed_nonce,
        generate_enc_params as _generate_enc_params,
        decrypt_rc4 as _decrypt_rc4,
    )
    _HAS_MICLOUD_SIGNING = True
except ImportError:
    _HAS_MICLOUD_SIGNING = False


class MiIOClient:
    """MiIO API 客户端（MIoT 协议，RC4 加密签名）"""

    def __init__(self, auth, store: XiaomiTokenStore, region: str = "cn"):
        self._auth = auth
        self._store = store
        self._region = region
        self._http = httpx.Client(timeout=15.0)
        base = f"https://{'' if region == 'cn' else region + '.'}api.io.mi.com/app"
        self._base_url = base

    def update_tokens(self, store: XiaomiTokenStore):
        self._store = store

    def close(self):
        self._http.close()

    def _build_cookies(self) -> dict:
        """构建 MiIO API cookies"""
        return {
            "userId": self._store.user_id,
            "yetAnotherServiceToken": self._store.xiaomiio_service_token,
            "serviceToken": self._store.xiaomiio_service_token,
            "locale": "zh_CN",
            "timezone": "GMT+08:00",
            "is_daylight": "0",
            "dst_offset": "0",
            "channel": "MI_APP_STORE",
            "PassportDeviceId": self._store.device_id or "",
        }

    def _miio_request(self, uri: str, payload: dict) -> Any:
        """MiIO API 请求（RC4 加密签名）"""
        if not _HAS_MICLOUD_SIGNING:
            raise Exception("需要 micloud 库: pip install micloud")

        ssecurity = self._store.xiaomiio_ssecurity
        url = f"{self._base_url}{uri}"

        # 用 RC4 加密签名
        params = {"data": json.dumps(payload)}
        nonce = _gen_nonce()
        sn = _signed_nonce(ssecurity, nonce)
        enc_params = _generate_enc_params(url, "POST", sn, nonce, params, ssecurity)

        cookies = self._build_cookies()
        headers = {
            "User-Agent": (
                "Android-7.1.1-1.0.0-ONEPLUS A3010-136-ABCDEF "
                "APP/xiaomi.smarthome APPV/62830"
            ),
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "content-type": "application/x-www-form-urlencoded",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }

        resp = self._http.post(url, data=enc_params, cookies=cookies, headers=headers)

        if resp.status_code != 200:
            raise Exception(f"MiIO API {resp.status_code}: {resp.text[:200]}")

        # 响应是 RC4 加密的，需要解密
        try:
            decrypted = _decrypt_rc4(_signed_nonce(ssecurity, nonce), resp.text)
            return json.loads(decrypted)
        except Exception:
            # 尝试直接解析 JSON（某些端点不加密）
            try:
                return resp.json()
            except Exception:
                raise Exception(f"无法解析 MiIO 响应: {resp.text[:200]}")

    def device_list_full(self) -> list[MiioDeviceInfo]:
        """获取完整设备列表"""
        result = self._miio_request("/home/device_list", {
            "getVirtualModel": False,
            "getHuamiDevices": 1,
        })
        devices = []
        for d in result.get("result", {}).get("list", []):
            devices.append(MiioDeviceInfo.from_dict(d))
        return devices

    def miot_get_props(self, params: list[dict]) -> list:
        """读取 MIoT 属性"""
        result = self._miio_request("/miotspec/prop/get", {"params": params})
        return result.get("result", [])

    def miot_set_props(self, params: list[dict]) -> list:
        """设置 MIoT 属性"""
        result = self._miio_request("/miotspec/prop/set", {"params": params})
        return result.get("result", [])

    def miot_action(self, did: str, siid: int, aiid: int, args: list = None) -> dict:
        """执行 MIoT 动作"""
        result = self._miio_request("/miotspec/action", {
            "params": {
                "did": did,
                "siid": siid,
                "aiid": aiid,
                "in": args or [],
            }
        })
        return result.get("result", {})

    def get_volume(self, did: str, features: SpeakerFeatureMap) -> int:
        """获取音量"""
        features.ensure_defaults()
        v = features.volume
        result = self.miot_get_props([{
            "did": did,
            "siid": v["siid"],
            "piid": v["piid"],
        }])
        if result and len(result) > 0:
            return result[0].get("value", 0)
        return 0

    def set_volume(self, did: str, volume: int, features: SpeakerFeatureMap):
        """设置音量"""
        features.ensure_defaults()
        v = features.volume
        self.miot_set_props([{
            "did": did,
            "siid": v["siid"],
            "piid": v["piid"],
            "value": volume,
        }])

    def get_mute(self, did: str, features: SpeakerFeatureMap) -> bool:
        """获取静音状态"""
        features.ensure_defaults()
        m = features.mute
        result = self.miot_get_props([{
            "did": did,
            "siid": m["siid"],
            "piid": m["piid"],
        }])
        if result and len(result) > 0:
            return bool(result[0].get("value", False))
        return False

    def execute_text_directive(self, did: str, text: str, features: SpeakerFeatureMap):
        """执行文本指令"""
        features.ensure_defaults()
        f = features.execute_text_directive
        args = [text]
        if "silent_piid" in f:
            # 可能需要设置静默参数
            pass
        self.miot_action(did, f["siid"], f["aiid"], args)

    def play_text(self, did: str, text: str, features: SpeakerFeatureMap):
        """播放文本（TTS）"""
        features.ensure_defaults()
        f = features.play_text
        self.miot_action(did, f["siid"], f["aiid"], [text])

    def wake_up(self, did: str, features: SpeakerFeatureMap):
        """唤醒设备"""
        features.ensure_defaults()
        f = features.wake_up
        self.miot_action(did, f["siid"], f["aiid"], f.get("ins", []))


class MiotSpecClient:
    """MIoT Spec 查询客户端"""

    def __init__(self):
        self._http = httpx.Client(timeout=15.0)
        self._instances_cache: list[dict] | None = None

    def close(self):
        self._http.close()

    def _get_instances(self) -> list[dict]:
        if self._instances_cache is None:
            resp = self._http.get(MIOT_SPEC_INSTANCES_URL)
            data = resp.json()
            self._instances_cache = data.get("instances", [])
        return self._instances_cache

    def get_type_for_model(self, model: str) -> str | None:
        """获取设备 model 对应的 MIoT spec type"""
        instances = self._get_instances()
        candidates = [i for i in instances if i.get("model") == model]
        if candidates:
            # 按版本降序排列，取最新的
            candidates.sort(key=lambda x: self._parse_version(x.get("type", "")), reverse=True)
            return candidates[0].get("type")
        return None

    def get_spec(self, model: str) -> dict | None:
        """获取设备 MIoT spec"""
        type_ = self.get_type_for_model(model)
        if not type_:
            return None
        resp = self._http.get(f"{MIOT_SPEC_INSTANCE_URL}{type_}")
        return resp.json()

    @staticmethod
    def _parse_version(type_urn: str) -> int:
        """从 URN 中解析版本号"""
        parts = type_urn.split(":")
        try:
            return int(parts[-1])
        except (ValueError, IndexError):
            return 0


def pick_speaker_features(spec: dict | None) -> SpeakerFeatureMap:
    """从 MIoT spec 中提取音箱功能映射"""
    features = SpeakerFeatureMap()

    if not spec or not spec.get("services"):
        features.ensure_defaults()
        return features

    SPEAKER_SERVICES = ["speaker"]
    PLAY_CONTROL_SERVICES = ["play_control", "play", "player", "playback_control"]
    INTELLIGENT_SPEAKER_SERVICES = ["intelligent_speaker"]
    VOLUME_PROPS = ["volume", "speaker_volume"]
    MUTE_PROPS = ["mute", "speaker_mute"]
    PLAY_ACTIONS = ["play"]
    PAUSE_ACTIONS = ["pause"]
    STOP_ACTIONS = ["stop"]
    WAKE_UP_ACTIONS = ["wake_up"]
    PLAY_TEXT_ACTIONS = ["play_text", "text_to_speech", "tts"]
    EXECUTE_TEXT_ACTIONS = ["execute_text_directive", "execute_directive"]
    SILENT_EXEC_PROPS = ["silent_execution"]

    for svc in spec.get("services", []):
        svc_type = svc.get("type", "").split(":")[-2] if ":" in svc.get("type", "") else ""
        svc_desc = svc.get("description", "").lower()

        # Volume
        if svc_type in SPEAKER_SERVICES or any(s in svc_desc for s in ["speaker"]):
            for prop in svc.get("properties", []):
                prop_desc = prop.get("description", "").lower()
                if any(p in prop_desc for p in VOLUME_PROPS) or prop.get("format") == "uint8":
                    if "volume" in prop_desc or not features.volume:
                        features.volume = {
                            "siid": svc["iid"],
                            "piid": prop["iid"],
                            "min": prop.get("value-range", [0, 100, 1])[0],
                            "max": prop.get("value-range", [0, 100, 1])[1],
                            "step": prop.get("value-range", [0, 100, 1])[2] if len(prop.get("value-range", [])) > 2 else 1,
                        }
                if any(p in prop_desc for p in MUTE_PROPS):
                    features.mute = {"siid": svc["iid"], "piid": prop["iid"]}

        # Play control
        if svc_type in PLAY_CONTROL_SERVICES or any(s in svc_desc for s in ["play", "player"]):
            for action in svc.get("actions", []):
                act_desc = action.get("description", "").lower()
                if any(a in act_desc for a in PLAY_ACTIONS) and not features.play:
                    features.play = {"siid": svc["iid"], "aiid": action["iid"]}
                if any(a in act_desc for a in PAUSE_ACTIONS) and not features.pause:
                    features.pause = {"siid": svc["iid"], "aiid": action["iid"]}
                if any(a in act_desc for a in STOP_ACTIONS) and not features.stop:
                    features.stop = {"siid": svc["iid"], "aiid": action["iid"]}

        # Intelligent speaker
        if svc_type in INTELLIGENT_SPEAKER_SERVICES or "intelligent" in svc_desc:
            silent_piid = None
            for prop in svc.get("properties", []):
                prop_desc = prop.get("description", "").lower()
                if any(p in prop_desc for p in SILENT_EXEC_PROPS):
                    silent_piid = prop["iid"]

            for action in svc.get("actions", []):
                act_desc = action.get("description", "").lower()
                if any(a in act_desc for a in WAKE_UP_ACTIONS) and not features.wake_up:
                    features.wake_up = {"siid": svc["iid"], "aiid": action["iid"]}
                if any(a in act_desc for a in PLAY_TEXT_ACTIONS) and not features.play_text:
                    features.play_text = {"siid": svc["iid"], "aiid": action["iid"]}
                if any(a in act_desc for a in EXECUTE_TEXT_ACTIONS) and not features.execute_text_directive:
                    features.execute_text_directive = {
                        "siid": svc["iid"],
                        "aiid": action["iid"],
                    }
                    if silent_piid:
                        features.execute_text_directive["silent_piid"] = silent_piid

    features.ensure_defaults()
    return features
