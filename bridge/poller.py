"""对话轮询器

核心轮询逻辑：
1. 定时轮询 MiNA 对话接口
2. 检测新的语音查询
3. 去重（时间戳 + requestId）
4. 唤醒词/对话窗口检测
5. 转发到 Hermes
6. 投递回复
"""

from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Optional

from xiaomi.models import (
    BridgeConfig, MinaDeviceInfo, SpeakerFeatureMap, ConversationRecord,
)
from xiaomi.mina import MiNAClient
from xiaomi.miio import MiIOClient, MiotSpecClient, pick_speaker_features
from xiaomi.auth import XiaomiAuthClient
from .hermes_client import HermesClient

logger = logging.getLogger("bridge.poller")


class VoicePoller:
    """语音对话轮询器"""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.running = False

        # 状态
        self.last_conversation_timestamp: int = 0
        self.last_conversation_request_id: str = ""
        self.last_conversation_query: str = ""
        self.last_dialog_window_opened_at: float = 0
        self.last_error: str = ""
        self.last_conversation_at: str = ""
        self.last_conversation_query_display: str = ""

        # 模式
        self.current_mode: str = config.mode

        # 唤醒词正则
        self.wake_word_regex = re.compile(config.wake_word_pattern)

        # 对话窗口
        self.continuous_dialog_window = config.dialog_window_seconds

        # 退避
        self._poll_transient_backoff_step = 0
        self._poll_transient_backoff_floor_ms = 0
        self._poll_transient_backoff_until = 0

        # 客户端（延迟初始化）
        self._auth: XiaomiAuthClient | None = None
        self._mina: MiNAClient | None = None
        self._miio: MiIOClient | None = None
        self._hermes: HermesClient | None = None
        self._device: MinaDeviceInfo | None = None
        self._features: SpeakerFeatureMap | None = None
        self._miot_did: str = ""
        self._hardware: str = ""

        # 轮询任务
        self._task: asyncio.Task | None = None

    def initialize(
        self,
        auth: XiaomiAuthClient,
        mina: MiNAClient,
        miio: MiIOClient,
        hermes: HermesClient,
        device: MinaDeviceInfo,
        miot_did: str = "",
        hardware: str = "",
    ):
        """初始化客户端"""
        self._auth = auth
        self._mina = mina
        self._miio = miio
        self._hermes = hermes
        self._device = device
        self._miot_did = miot_did or device.miot_did
        self._hardware = hardware or device.hardware

        # 探测 MIoT spec
        if self._miot_did:
            try:
                spec_client = MiotSpecClient()
                spec = spec_client.get_spec(device.model) if device.model else None
                self._features = pick_speaker_features(spec)
                spec_client.close()
                logger.info(f"MIoT spec 已加载，功能映射: volume={self._features.volume}")
            except Exception as e:
                logger.warning(f"MIoT spec 加载失败，使用默认: {e}")
                self._features = SpeakerFeatureMap()
                self._features.ensure_defaults()

    async def start(self):
        """启动轮询"""
        if self.running:
            return
        self.running = True
        logger.info(f"语音轮询已启动 (模式: {self.current_mode}, 间隔: {self.config.poll_interval_ms}ms)")
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        """停止轮询"""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("语音轮询已停止")

    async def _poll_loop(self):
        """轮询循环"""
        while self.running:
            cycle_start = time.time()
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                msg = str(e)
                self.last_error = msg
                logger.error(f"轮询异常: {msg}")

            # 计算下次轮询间隔
            if self.running:
                interval_s = self.config.poll_interval_ms / 1000.0
                elapsed = time.time() - cycle_start
                wait = max(0, interval_s - elapsed)

                # 退避处理
                if self._poll_transient_backoff_until > time.time():
                    wait = max(wait, self._poll_transient_backoff_floor_ms / 1000.0)

                await asyncio.sleep(wait)

    async def _poll_once(self):
        """单次轮询"""
        if not self._mina or not self._device:
            return

        # 异步执行同步的 HTTP 轮询
        record = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._mina.fetch_conversation(
                self._hardware, self._device.device_id
            ),
        )

        if not record or not record.query:
            return

        # 去重
        is_duplicate = (
            record.time < self.last_conversation_timestamp or
            (record.time == self.last_conversation_timestamp and
             record.request_id and record.request_id == self.last_conversation_request_id) or
            (record.time == self.last_conversation_timestamp and
             not record.request_id and record.query == self.last_conversation_query)
        )

        if is_duplicate:
            return

        # 更新状态
        self.last_conversation_timestamp = record.time
        self.last_conversation_request_id = record.request_id
        self.last_conversation_query = record.query
        self.last_conversation_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_conversation_query_display = record.query

        # 检查是否是启动时的旧记录（超过 30 秒的）
        record_age_ms = max(0, time.time() * 1000 - record.time)
        if record_age_ms > 30000 and record.answers:
            logger.debug(f"忽略启动旧记录: {record.query[:50]} (age={record_age_ms:.0f}ms)")
            return

        # 检查是否是自己触发的
        if self._should_ignore_self_triggered(record.query):
            return

        # 记录
        logger.info(f"← [语音识别] \"{record.query}\" | 模式: {self.current_mode}")

        for answer in record.answers:
            logger.info(f"  小爱回复: {answer[:80]}")

        # 处理
        await self._handle_incoming_query(record.query, record)

    def _should_ignore_self_triggered(self, query: str) -> bool:
        """检查是否是自己触发的语音（避免循环）"""
        # 如果桥接本身在播报，需要忽略自己触发的对话
        # 通过短暂的时间窗口来判断
        return False

    async def _handle_incoming_query(self, query: str, record: ConversationRecord):
        """处理新的语音查询"""
        mode = self.current_mode

        if mode == "silent":
            logger.info("   [静默] 跳过，不拦截")
            return

        if mode == "proxy":
            logger.info("   [代理] 拦截所有对话")
            await self._intercept_and_forward(query)
            return

        # wake 模式
        current_time = time.time()
        time_since_window = current_time - self.last_dialog_window_opened_at
        is_wake_triggered = bool(self.wake_word_regex.search(query))
        is_continuous = (
            self.last_dialog_window_opened_at > 0 and
            time_since_window <= self.continuous_dialog_window
        )

        if is_wake_triggered or is_continuous:
            # 打开对话窗口
            self.last_dialog_window_opened_at = current_time
            logger.info(f"   [唤醒] 捕获: \"{query[:50]}\" (唤醒词: {is_wake_triggered}, 免唤醒: {is_continuous})")
            await self._intercept_and_forward(query)
        else:
            logger.debug(f"   [未匹配] \"{query[:50]}\" (不在唤醒词或对话窗口内)")

    async def _intercept_and_forward(self, query: str):
        """拦截查询，转发到 Hermes，投递回复"""
        # 确保 Hermes 和音箱都就绪
        if not self._hermes or not self._mina or not self._device:
            logger.warning("客户端未就绪，跳过")
            return

        try:
            # 先暂停小爱原生播放（避免语音重叠）
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.player_pause(self._device.device_id)
            )
        except Exception as e:
            logger.debug(f"暂停播放失败（可忽略）: {e}")

        # 发送到 Hermes
        try:
            reply = await self._hermes.chat(query)
        except Exception as e:
            logger.error(f"Hermes 请求失败: {e}")
            reply = "抱歉，我暂时无法回答"
            self.last_error = str(e)

        # 投递回复
        await self._deliver_reply(reply)

    async def _deliver_reply(self, text: str):
        """将回复投递到音箱"""
        if not self._mina or not self._device:
            return

        try:
            # 先清空当前播放（避免残留音乐干扰）
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.player_stop(self._device.device_id)
            )
            await asyncio.sleep(0.3)

            # TTS 播报
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.text_to_speech(self._device.device_id, text)
            )
            logger.info(f"→ [TTS] \"{text[:80]}...\"")
        except Exception as e:
            logger.error(f"投递回复失败: {e}")

    # === 外部控制接口 ===

    async def speak(self, text: str):
        """主动播报文本"""
        if self._mina and self._device:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.text_to_speech(self._device.device_id, text)
            )

    async def play_audio(self, url: str):
        """播放音频URL"""
        if self._mina and self._device:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.play_url(self._device.device_id, url)
            )

    async def execute_command(self, text: str):
        """执行语音指令"""
        if self._mina and self._device:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.execute_text_directive(self._device.device_id, text)
            )

    async def set_volume(self, volume: int):
        """设置音量"""
        if self._mina and self._device:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._mina.set_volume(self._device.device_id, volume)
            )

    async def set_mode(self, mode: str):
        """切换模式"""
        if mode in ("wake", "proxy", "silent"):
            self.current_mode = mode
            self.config.mode = mode
            logger.info(f"模式已切换为: {mode}")
        else:
            raise ValueError(f"无效模式: {mode} (可选: wake, proxy, silent)")

    def reset_voice_session(self):
        """重置语音会话"""
        if self._hermes:
            self._hermes.reset_conversation()
        self.last_dialog_window_opened_at = 0
        logger.info("语音会话已重置")

    def get_status(self) -> dict:
        """获取当前状态"""
        return {
            "running": self.running,
            "mode": self.current_mode,
            "device": {
                "name": self._device.name if self._device else "",
                "hardware": self._device.hardware if self._device else "",
                "device_id": self._device.device_id if self._device else "",
            } if self._device else None,
            "last_conversation_at": self.last_conversation_at,
            "last_conversation_query": self.last_conversation_query_display,
            "last_error": self.last_error,
            "poll_interval_ms": self.config.poll_interval_ms,
            "wake_word_pattern": self.config.wake_word_pattern,
            "dialog_window_seconds": self.config.dialog_window_seconds,
        }
