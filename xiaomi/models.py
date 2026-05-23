"""小米账号与设备数据模型"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class XiaomiTokenStore:
    """小米账号登录态"""
    user_id: str = ""
    c_user_id: str = ""
    pass_token: str = ""
    device_id: str = ""
    # micoapi: (ssecurity, serviceToken)
    micoapi_ssecurity: str = ""
    micoapi_service_token: str = ""
    # xiaomiio: (ssecurity, serviceToken)
    xiaomiio_ssecurity: str = ""
    xiaomiio_service_token: str = ""

    def is_valid(self) -> bool:
        return bool(self.user_id and self.micoapi_service_token)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "c_user_id": self.c_user_id,
            "pass_token": self.pass_token,
            "device_id": self.device_id,
            "micoapi_ssecurity": self.micoapi_ssecurity,
            "micoapi_service_token": self.micoapi_service_token,
            "xiaomiio_ssecurity": self.xiaomiio_ssecurity,
            "xiaomiio_service_token": self.xiaomiio_service_token,
        }

    @classmethod
    def from_dict(cls, d: dict) -> XiaomiTokenStore:
        return cls(
            user_id=d.get("user_id", ""),
            c_user_id=d.get("c_user_id", ""),
            pass_token=d.get("pass_token", ""),
            device_id=d.get("device_id", ""),
            micoapi_ssecurity=d.get("micoapi_ssecurity", ""),
            micoapi_service_token=d.get("micoapi_service_token", ""),
            xiaomiio_ssecurity=d.get("xiaomiio_ssecurity", ""),
            xiaomiio_service_token=d.get("xiaomiio_service_token", ""),
        )


@dataclass
class MinaDeviceInfo:
    """MiNA 设备信息"""
    device_id: str = ""
    hardware: str = ""
    miot_did: str = ""
    alias: str = ""
    name: str = ""
    model: str = ""
    mac: str = ""
    ip: str = ""
    is_active: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> MinaDeviceInfo:
        return cls(
            device_id=d.get("deviceID", d.get("device_id", "")),
            hardware=d.get("hardware", ""),
            miot_did=str(d.get("miotDID", d.get("miot_did", ""))),
            alias=d.get("alias", ""),
            name=d.get("name", ""),
            model=d.get("model", ""),
            mac=d.get("mac", ""),
            ip=d.get("ip", ""),
            is_active=d.get("isActivate", False),
            raw=d,
        )


@dataclass
class MiioDeviceInfo:
    """MiIO 设备信息"""
    did: str = ""
    name: str = ""
    model: str = ""
    token: str = ""
    ip: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> MiioDeviceInfo:
        return cls(
            did=str(d.get("did", "")),
            name=d.get("name", ""),
            model=d.get("model", ""),
            token=d.get("token", ""),
            ip=d.get("ip", ""),
            raw=d,
        )


@dataclass
class SpeakerFeatureMap:
    """音箱 MIoT 功能映射"""
    volume: Optional[dict] = None    # {siid, piid, min, max, step}
    mute: Optional[dict] = None      # {siid, piid}
    play: Optional[dict] = None      # {siid, aiid}
    pause: Optional[dict] = None     # {siid, aiid}
    stop: Optional[dict] = None      # {siid, aiid}
    wake_up: Optional[dict] = None   # {siid, aiid}
    play_text: Optional[dict] = None # {siid, aiid}
    execute_text_directive: Optional[dict] = None  # {siid, aiid, silent_piid}

    # 默认值（兼容大多数小爱音箱）
    DEFAULT_VOLUME = {"siid": 2, "piid": 1, "min": 0, "max": 100, "step": 1}
    DEFAULT_MUTE = {"siid": 2, "piid": 2}
    DEFAULT_PLAY = {"siid": 3, "aiid": 2}
    DEFAULT_PAUSE = {"siid": 3, "aiid": 3}
    DEFAULT_STOP = {"siid": 3, "aiid": 4}
    DEFAULT_WAKE_UP = {"siid": 5, "aiid": 1}
    DEFAULT_PLAY_TEXT = {"siid": 5, "aiid": 3}
    DEFAULT_EXECUTE_TEXT_DIRECTIVE = {"siid": 5, "aiid": 4, "silent_piid": 2}

    def ensure_defaults(self):
        if not self.volume:
            self.volume = self.DEFAULT_VOLUME.copy()
        if not self.mute:
            self.mute = self.DEFAULT_MUTE.copy()
        if not self.play:
            self.play = self.DEFAULT_PLAY.copy()
        if not self.pause:
            self.pause = self.DEFAULT_PAUSE.copy()
        if not self.stop:
            self.stop = self.DEFAULT_STOP.copy()
        if not self.wake_up:
            self.wake_up = self.DEFAULT_WAKE_UP.copy()
        if not self.play_text:
            self.play_text = self.DEFAULT_PLAY_TEXT.copy()
        if not self.execute_text_directive:
            self.execute_text_directive = self.DEFAULT_EXECUTE_TEXT_DIRECTIVE.copy()


@dataclass
class ConversationRecord:
    """对话轮询记录"""
    query: str = ""
    answer: str = ""
    time: int = 0
    request_id: str = ""
    answers: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> ConversationRecord:
        answers = []
        if "answers" in d and isinstance(d["answers"], list):
            for a in d["answers"]:
                if isinstance(a, dict) and "tts" in a:
                    tts_val = a["tts"]
                    if isinstance(tts_val, dict):
                        # tts 是嵌套 dict: {"text": "...", ...}
                        answers.append(tts_val.get("text", ""))
                    elif isinstance(tts_val, str):
                        answers.append(tts_val)
                elif isinstance(a, dict) and "text" in a:
                    answers.append(a["text"])
                elif isinstance(a, str):
                    answers.append(a)
        elif "answer" in d:
            answers = [d["answer"]] if d["answer"] else []

        return cls(
            query=d.get("query", ""),
            answer=d.get("answer", ""),
            time=int(d.get("time", 0)),
            request_id=d.get("requestId", d.get("request_id", "")),
            answers=answers,
            raw=d,
        )


@dataclass
class BridgeConfig:
    """桥接配置"""
    # 小米账号
    account: str = ""
    password: str = ""
    server_country: str = "cn"

    # 设备
    hardware: str = ""           # 音箱 hardware ID
    speaker_name: str = ""      # 音箱名称
    mi_did: str = ""            # MiIO DID
    mina_device_id: str = ""    # MiNA deviceID

    # 路径
    token_store_path: str = ""
    state_store_path: str = ""

    # 轮询
    poll_interval_ms: int = 1000

    # LLM
    api_url: str = "http://127.0.0.1:9222"
    api_key: str = ""
    model: str = "mimo-v2.5"
    system_prompt: str = (
        "你正在通过真实小爱音箱实时语音对话。目标是尽快口头回答。"
        "回答尽量简短自然，像在和人说话一样。"
        "不要输出markdown、代码块、工具回执或流程确认，只给用户真正需要听到的内容。"
    )

    # 语音模式: wake(唤醒词), proxy(代理所有), silent(静默)
    mode: str = "wake"

    # 唤醒词
    wake_word_pattern: str = r"(贾维斯|jarvis|Jarvis|JARVIS)"

    # 对话窗口（秒）
    dialog_window_seconds: int = 30

    # Web 控制台
    console_host: str = "0.0.0.0"
    console_port: int = 8199

    # 音频
    audio_public_base_url: str = ""
    audio_tail_padding_ms: int = 500

    # 语音上下文
    voice_context_max_turns: int = 6
    voice_context_max_chars: int = 4000

    # 过渡语句（小爱原生回答后检测到的关键词，用于判断是否是真正的问题）
    transition_phrases: list = field(default_factory=lambda: [
        "让我想想", "我来帮你", "正在为你", "好的，", "没问题",
        "收到", "马上", "这就", "正在处理",
    ])

    # 非流式兜底
    force_non_streaming: bool = False

    # 调试
    debug_log_enabled: bool = True
    debug_log_path: str = ""
