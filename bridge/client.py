"""LLM API 客户端

通过 OpenAI-compatible API 与 LLM 对话。
"""

from __future__ import annotations
import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("bridge.client")


class LLMClient:
    """LLM OpenAI-compatible API 客户端"""

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:9222",
        api_key: str = "",
        model: str = "mimo-v2.5",
        system_prompt: str = "",
        max_turns: int = 6,
        max_chars: int = 4000,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.max_chars = max_chars

        self._http = httpx.AsyncClient(timeout=120.0)
        self._conversation_history: list[dict] = []

    async def close(self):
        await self._http.aclose()

    def reset_conversation(self):
        """重置对话历史"""
        self._conversation_history = []
        logger.info("对话历史已重置")

    def _build_messages(self, user_query: str) -> list[dict]:
        """构建消息列表"""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # 添加历史对话
        for msg in self._conversation_history:
            messages.append(msg)

        # 添加当前用户消息
        messages.append({"role": "user", "content": user_query})

        # 截断历史，确保不超过 max_turns 轮
        # system message + history + current = total
        if self.system_prompt:
            non_system = messages[1:]  # 去掉 system
            if len(non_system) > self.max_turns * 2:
                non_system = non_system[-(self.max_turns * 2):]
            messages = [messages[0]] + non_system
        else:
            if len(messages) > self.max_turns * 2:
                messages = messages[-(self.max_turns * 2):]

        # 截断过长内容
        total_chars = sum(len(m.get("content", "")) for m in messages)
        while total_chars > self.max_chars and len(messages) > 2:
            # 从历史中移除最早的消息（保留 system 和最后的 user）
            removed = messages[1 if self.system_prompt else 0]
            total_chars -= len(removed.get("content", ""))
            messages.pop(1 if self.system_prompt else 0)

        return messages

    async def chat(self, query: str) -> str:
        """
        发送对话请求，返回回复文本。
        """
        messages = self._build_messages(query)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 获取当前日期信息注入上下文
        import datetime
        now = datetime.datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        date_info = f"当前日期: {now.strftime('%Y年%m月%d日')} {weekdays[now.weekday()]} {now.strftime('%H:%M')}"

        # 在 system prompt 后面注入日期上下文
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += f"\n\n{date_info}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 512,
        }

        try:
            resp = await self._http.post(
                f"{self.api_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            reply = data["choices"][0]["message"]["content"]

            # 后处理：强制替换身份（mimo 模型被训练成认同"小爱"，system prompt 无法覆盖）
            reply = self._fix_identity(reply)

            # 更新对话历史
            self._conversation_history.append({"role": "user", "content": query})
            self._conversation_history.append({"role": "assistant", "content": reply})

            # 截断历史
            if len(self._conversation_history) > self.max_turns * 2:
                self._conversation_history = self._conversation_history[-(self.max_turns * 2):]

            logger.info(f"LLM 回复 [{len(reply)}字]: {reply[:80]}...")
            return reply

        except httpx.HTTPStatusError as e:
            logger.error(f"LLM API 错误 {e.response.status_code}: {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"LLM API 请求失败: {e}")
            raise

    @staticmethod
    def _fix_identity(text: str) -> str:
        """替换模型回复中的'小爱'为'贾维斯'，处理否定句等边界情况"""
        import re

        # 先检查原始文本中是否有身份否定句（在替换之前检测）
        # 模型可能说"不是贾维斯"、"我不是钢铁侠的贾维斯"等
        identity_negative_patterns = [
            r"不是贾维斯",           # "不是贾维斯哦"
            r"不是钢铁侠",           # "不是钢铁侠的贾维斯"
            r"我是小爱.*不是",        # "我是小爱，不是..."
            r"我不是.*贾维斯",        # "我不是贾维斯"
        ]
        for pattern in identity_negative_patterns:
            if re.search(pattern, text):
                return "我是贾维斯，随时为您服务。有什么可以帮你的？"

        # 正常替换
        text = text.replace("我是小爱同学", "我是贾维斯")
        text = text.replace("我是小爱", "我是贾维斯")
        text = text.replace("小爱同学", "贾维斯")
        text = text.replace("小爱", "贾维斯")
        text = text.replace("贾维斯贾维斯", "贾维斯")
        return text

    @staticmethod
    def clean_for_tts(text: str) -> str:
        """清理文本用于 TTS 播报（去掉换行、markdown 等）"""
        import re
        # 去掉换行，替换为空格
        text = text.replace("\n", " ")
        # 去掉多余空格
        text = re.sub(r"\s+", " ", text).strip()
        # 去掉 markdown 标记
        text = re.sub(r"\*+", "", text)
        text = re.sub(r"#+\s*", "", text)
        # 去掉 emoji
        text = re.sub(r"[\U0001F600-\U0001F9FF]", "", text)
        return text
