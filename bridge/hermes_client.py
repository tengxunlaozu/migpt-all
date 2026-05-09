"""Hermes API 客户端

通过 OpenAI-compatible API 与 Hermes 对话。
"""

from __future__ import annotations
import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("bridge.hermes")


class HermesClient:
    """Hermes OpenAI-compatible API 客户端"""

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

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
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

            # 更新对话历史
            self._conversation_history.append({"role": "user", "content": query})
            self._conversation_history.append({"role": "assistant", "content": reply})

            # 截断历史
            if len(self._conversation_history) > self.max_turns * 2:
                self._conversation_history = self._conversation_history[-(self.max_turns * 2):]

            logger.info(f"Hermes 回复 [{len(reply)}字]: {reply[:80]}...")
            return reply

        except httpx.HTTPStatusError as e:
            logger.error(f"Hermes API 错误 {e.response.status_code}: {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"Hermes API 请求失败: {e}")
            raise
