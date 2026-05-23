# 小爱-LLM 桥接服务 完整部署教程

> 让小爱音箱成为你的语音 Agent 入口 —— 对小爱说话，LLM 回答，小爱播报。

---

## 目录

1. [项目简介](#1-项目简介)
2. [功能清单](#2-功能清单)
3. [环境要求](#3-环境要求)
4. [安装部署](#4-安装部署)
5. [配置详解](#5-配置详解)
6. [首次启动与登录](#6-首次启动与登录)
7. [使用说明](#7-使用说明)
8. [语音模式详解](#8-语音模式详解)
9. [Web 控制台](#9-web-控制台)
10. [systemd 服务](#10-systemd-服务)
11. [命令行参数](#11-命令行参数)
12. [配置文件参考](#12-配置文件参考)
13. [目录结构](#13-目录结构)
14. [故障排查](#14-故障排查)
15. [与原项目对比](#15-与原项目对比)
16. [常见问题](#16-常见问题)

---

## 1. 项目简介

### 这是什么

**小爱-LLM 桥接服务** 是一个 Python 服务，把小爱音箱接入 LLM Agent，实现：

- 对小爱音箱说话 → 语音被识别 → 转发给 LLM LLM → 回复通过小爱播报
- 通过 Web 控制台管理设备、切换模式、查看日志
- 支持唤醒词拦截、代理模式、静默模式

### 项目来源

本项目功能对标 [Xiaoai-Claw-Addon](https://github.com/ZhengXieGang/Xiaoai-Claw-Addon)（OpenClaw 插件），使用 Python 重新实现，适配 LLM Agent。

核心区别：
- 原项目是 TypeScript + OpenClaw 插件架构
- 本项目是纯 Python + 独立服务 + Web 控制台
- 适配你的小爱音箱 Play 增强版 (L05C)

### 工作原理

```
┌──────────┐     语音      ┌──────────────┐    HTTP     ┌───────────┐
│ 你(用户)  │ ────────────→ │  小爱音箱     │             │ 小米云端   │
│          │               │  (L05C)      │             │ MiNA API  │
└──────────┘               └──────┬───────┘             └─────┬─────┘
                                  │                           │
                                  │  云端对话记录              │
                                  │ ←─────────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  桥接服务 (本项目)          │
                    │                           │
                    │  1. 轮询 MiNA 对话接口     │
                    │  2. 检测到新语音查询        │
                    │  3. 发送到 LLM API      │
                    │  4. 收到 LLM 回复          │
                    │  5. TTS 播报到音箱          │
                    └─────────────┬─────────────┘
                                  │  HTTP
                    ┌─────────────▼─────────────┐
                    │  LLM API Server        │
                    │  (hermes serve :9222)     │
                    └───────────────────────────┘
```

---

## 2. 功能清单

| 功能 | 说明 | 状态 |
|------|------|------|
| 语音对话 | 对小爱说话 → LLM 回答 → 小爱播报 | ✅ |
| TTS 播报 | 任意文本让小爱说出来 | ✅ |
| 音频播放 | 播放 URL 音频到音箱 | ✅ |
| 指令执行 | 远程执行小爱语音指令 | ✅ |
| 音量控制 | 远程调节音量 0-100 | ✅ |
| 唤醒词拦截 | 检测到唤醒词才转发（默认"贾维斯"） | ✅ |
| 代理模式 | 拦截所有小爱语音对话 | ✅ |
| 静默模式 | 不拦截，只保留主动播报 | ✅ |
| 对话上下文 | 记住同一轮对话的上下文 | ✅ |
| Web 控制台 | 浏览器管理界面 | ✅ |
| 持久化配置 | 登录态、设备、配置自动保存 | ✅ |
| 调试日志 | 详细运行日志 | ✅ |
| MIoT Spec | 自动探测音箱 MIoT 能力 | ✅ |
| 自动恢复 | 重启后自动恢复登录态和设备 | ✅ |

---

## 3. 环境要求

### 服务器

| 项目 | 要求 |
|------|------|
| 操作系统 | Linux (Debian/Ubuntu/CentOS 均可) |
| Python | >= 3.10 |
| 内存 | >= 512MB |
| 网络 | 能访问小米云端 API 和 LLM API |

### 你的账号

| 项目 | 说明 |
|------|------|
| 小米账号 | 手机号或邮箱 |
| 小米密码 | 账号密码 |
| 小爱音箱 | 需要已绑定到你的小米账号 |
| LLM API | 需要 LLM API Server 运行中 (`hermes serve`) |

---

## 4. 安装部署

### 4.1 安装 Python 依赖

```bash
# 进入项目目录
cd ~/xiaomi-llm-bridge

# 安装依赖
pip3 install -r requirements.txt
```

依赖列表：
- `httpx` — HTTP 客户端（登录认证用）
- `aiohttp` — 异步 HTTP（备用）
- `fastapi` + `uvicorn` — Web 控制台
- `jinja2` — HTML 模板
- `python-multipart` — 表单处理
- `pyyaml` — 配置文件解析

### 4.2 复制配置文件

```bash
cp config.yaml.example config.yaml
```

### 4.3 编辑配置

```bash
nano config.yaml
```

最关键的配置项（见下方[配置详解](#5-配置详解)）：

```yaml
# 必填：LLM API 地址
api_url: "http://127.0.0.1:9222"

# 可选：通过环境变量设置小米账号
# 或者通过 Web 控制台登录（推荐）
```

---

## 5. 配置详解

### 5.1 小米账号

**方式一：Web 控制台登录（推荐）**

不在配置文件中写密码，通过浏览器登录，更安全。

**方式二：配置文件**

```yaml
account: "your_xiaomi_account"    # 手机号或邮箱
password: "your_password"         # 密码
```

**方式三：环境变量**

```bash
export XIAOMI_ACCOUNT="your_account"
export XIAOMI_PASSWORD="your_password"
```

优先级：环境变量 > 配置文件 > Web 控制台手动登录

### 5.2 LLM API

```yaml
# LLM API 地址（需要先启动 hermes serve）
api_url: "http://127.0.0.1:9222"

# API Key（如果配置了认证）
api_key: ""

# 使用的模型名
model: "mimo-v2.5"
```

**重要：** 你需要先在另一个终端启动 LLM API Server：

```bash
hermes serve
# 默认监听 9222 端口
```

### 5.3 语音模式

```yaml
# wake  — 唤醒词模式（默认，推荐）
# proxy — 代理模式（拦截所有对话）
# silent — 静默模式（不拦截，只保留主动播报）
mode: "wake"
```

详见 [语音模式详解](#8-语音模式详解)。

### 5.4 唤醒词

```yaml
# 正则表达式，匹配小爱识别到的语音开头
# 默认匹配"贾维斯"或"Jarvis"
wake_word_pattern: "^(贾维斯| Jarvis)"

# 示例：
# "^(嘿贾维斯|贾维斯| Hey Jarvis| Jarvis)"
# "^(小爱同学)"  — 拦截所有"小爱同学"开头的对话
```

### 5.5 对话窗口

```yaml
# 唤醒后持续监听的时间（秒）
# 在此期间不需要再次唤醒
dialog_window_seconds: 30

# 轮询间隔（毫秒），越小越灵敏，但消耗更多 API 调用
poll_interval_ms: 1000
```

### 5.6 系统提示词

```yaml
system_prompt: |
  你正在通过真实小爱音箱实时语音对话。
  目标是尽快口头回答。
  回答尽量简短自然，像在和人说话一样。
  不要输出markdown、代码块、工具回执或流程确认，
  只给用户真正需要听到的内容。
```

可以根据需要修改，比如：

```yaml
system_prompt: |
  你是贾维斯，Tony 的智能管家。
  通过小爱音箱和 Tony 对话。
  回答简洁，像管家一样专业。
  如果不确定就直说不知道。
```

### 5.7 语音上下文

```yaml
# 保留最近几轮对话上下文
voice_context_max_turns: 6

# 上下文总字符数上限
voice_context_max_chars: 4000
```

### 5.8 Web 控制台

```yaml
# 监听地址（0.0.0.0 允许外部访问）
console_host: "0.0.0.0"

# 端口
console_port: 8199
```

### 5.9 调试日志

```yaml
debug_log_enabled: true
# 日志保存在 ~/.xiaomi-llm-bridge/debug.log
```

---

## 6. 首次启动与登录

### 6.1 启动服务

```bash
cd ~/xiaomi-llm-bridge
python3 main.py
```

你会看到类似这样的输出：

```
==================================================
  小爱 ↔ LLM 桥接服务
==================================================
控制台地址: http://0.0.0.0:8199
LLM API: http://127.0.0.1:9222
模型: mimo-v2.5
语音模式: wake
唤醒词: ^(贾维斯| Jarvis)
INFO:     Uvicorn running on http://0.0.0.0:8199
```

### 6.2 打开控制台

浏览器访问：`http://你的服务器IP:8199`

### 6.3 登录小米账号

1. 在控制台页面输入小米账号和密码
2. 点击"登录"
3. 如果账号有安全验证（手机短信/邮箱验证码），可能需要在终端中处理验证流程

**安全验证处理：**

如果登录时出现安全验证，服务端日志会打印验证相关信息。需要根据提示完成验证（输入验证码等）。

### 6.4 选择设备

1. 登录成功后，控制台会显示你的设备列表
2. 找到你的小爱音箱 Play 增强版
3. 点击选中，然后点"选中此设备"

### 6.5 启动轮询

1. 选好设备后，点击"▶ 启动"
2. 服务开始轮询小米云端对话接口
3. 对小爱说"贾维斯你好"，看日志是否有响应

### 6.6 验证

```bash
# 查看实时日志
tail -f ~/.xiaomi-llm-bridge/debug.log
```

对小爱说话，观察日志中是否出现：

```
← [语音识别] "贾维斯你好" | 模式: wake
[唤醒] 捕获: "贾维斯你好" (唤醒词: True, 免唤醒: False)
→ [TTS] "你好托尼，有什么需要帮忙的吗？..."
```

---

## 7. 使用说明

### 日常使用流程

1. 确保 LLM API Server 在运行：`hermes serve`
2. 启动桥接服务：`cd ~/xiaomi-llm-bridge && python3 main.py`
3. 对小爱说"贾维斯 + 你的问题"
4. 等待回答通过小爱播报出来

### 快捷指令

```bash
# 后台启动
nohup python3 main.py &

# 或用 systemd（推荐，见第10节）
sudo systemctl start xiaomi-llm
```

### 通过控制台主动播报

在 Web 控制台的"播报"输入框中输入文本，点"🗣 播报"，小爱会立即说出来。

### 通过控制台执行指令

在"执行"输入框中输入小爱指令，比如：
- "今天天气"
- "播放音乐"
- "关灯"

### 切换模式

在控制台点对应按钮：
- **唤醒模式** — 默认，需要说唤醒词
- **代理模式** — 所有对话都被拦截
- **静默模式** — 不拦截，安静运行

---

## 8. 语音模式详解

### 唤醒模式 (wake) — 默认

```
用户: "小爱，今天天气"     → 不拦截（小爱原生回答）
用户: "贾维斯，今天天气"   → 拦截，转发到 LLM
用户: "那明天呢"           → 拦截（30秒窗口期内免唤醒）
(30秒后)
用户: "贾维斯，谢谢"       → 需要重新说唤醒词
```

**适用场景：** 日常使用，小爱原有功能和 Agent 功能共存。

### 代理模式 (proxy)

```
用户: "小爱，今天天气"     → 拦截，转发到 LLM
用户: "贾维斯你好"         → 拦截，转发到 LLM
用户: "播放音乐"           → 拦截，转发到 LLM（注意：不是真正的播放指令）
```

**适用场景：** 完全由 Agent 接管，不使用小爱原有功能。

### 静默模式 (silent)

```
用户: "小爱，今天天气"     → 不拦截（小爱原生回答）
用户: "贾维斯你好"         → 不拦截
但可以通过控制台主动播报/执行
```

**适用场景：** 只需要主动控制能力，不需要语音拦截。

---

## 9. Web 控制台

### 界面说明

| 区域 | 功能 |
|------|------|
| 登录区 | 输入小米账号密码登录 |
| 设备选择 | 列出所有绑定的小爱设备 |
| 状态区 | 显示设备信息、运行状态、最近对话 |
| 模式切换 | wake / proxy / silent |
| 播报输入 | 输入文本让小爱说出来 |
| 指令执行 | 执行小爱语音指令 |
| 音量控制 | 设置音量 0-100 |
| 会话重置 | 重置对话历史 |
| 调试日志 | 查看运行日志 |

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 获取当前状态 |
| POST | `/api/login` | 登录小米账号 |
| POST | `/api/logout` | 登出 |
| GET | `/api/devices` | 获取设备列表 |
| POST | `/api/select_device` | 选择设备 |
| POST | `/api/start` | 启动轮询 |
| POST | `/api/stop` | 停止轮询 |
| POST | `/api/mode` | 切换模式 `{"mode":"wake"}` |
| POST | `/api/speak` | 播报 `{"text":"你好"}` |
| POST | `/api/execute` | 执行指令 `{"text":"播放音乐"}` |
| POST | `/api/volume` | 音量 `{"volume":50}` |
| POST | `/api/reset_session` | 重置会话 |
| GET | `/api/debug_log` | 获取日志 |

---

## 10. systemd 服务

### 安装为系统服务

```bash
# 复制 service 文件
sudo cp ~/xiaomi-llm-bridge/xiaomi-llm.service /etc/systemd/system/

# 重新加载
sudo systemctl daemon-reload

# 开机自启
sudo systemctl enable xiaomi-llm

# 启动
sudo systemctl start xiaomi-llm
```

### 管理服务

```bash
# 查看状态
sudo systemctl status xiaomi-llm

# 查看日志
sudo journalctl -u xiaomi-llm -f

# 重启
sudo systemctl restart xiaomi-llm

# 停止
sudo systemctl stop xiaomi-llm
```

### 如果要修改 service 文件

```bash
# 编辑
sudo nano /etc/systemd/system/xiaomi-llm.service

# 重载
sudo systemctl daemon-reload
sudo systemctl restart xiaomi-llm
```

### 通过环境变量配置账号

编辑 service 文件，在 `[Service]` 段添加：

```ini
Environment=XIAOMI_ACCOUNT=your_account
Environment=XIAOMI_PASSWORD=your_password
Environment=LLM_API_URL=http://127.0.0.1:9222
```

---

## 11. 命令行参数

```bash
python3 main.py [选项]
```

| 参数 | 短参数 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--console-only` | | 只启动控制台，不自动轮询 |
| `--verbose` | `-v` | 输出详细日志（DEBUG级别） |
| `--port PORT` | `-p` | 覆盖控制台端口 |

### 示例

```bash
# 默认启动
python3 main.py

# 指定配置文件
python3 main.py -c /path/to/config.yaml

# 只启动控制台（适合首次登录配置）
python3 main.py --console-only

# 指定端口 + 详细日志
python3 main.py -p 8080 -v

# 后台运行
nohup python3 main.py > /dev/null 2>&1 &
```

---

## 12. 配置文件参考

完整配置文件 `config.yaml.example`：

```yaml
# ═══════════════════════════════════════
# 小爱-LLM 桥接服务 配置
# ═══════════════════════════════════════

# ─── 小米账号 ───
account: ""                    # 小米账号（手机/邮箱）
password: ""                   # 密码
server_country: "cn"           # 服务器地区（cn=中国）

# ─── 语音模式 ───
mode: "wake"                   # wake | proxy | silent
wake_word_pattern: "^(贾维斯| Jarvis)"  # 唤醒词正则
dialog_window_seconds: 30      # 对话窗口（秒）
poll_interval_ms: 1000         # 轮询间隔（毫秒）

# ─── LLM API ───
api_url: "http://127.0.0.1:9222"  # API 地址
api_key: ""                        # API Key
model: "mimo-v2.5"                 # 模型名

# ─── 系统提示词 ───
system_prompt: |
  你正在通过真实小爱音箱实时语音对话。
  目标是尽快口头回答。
  回答尽量简短自然，像在和人说话一样。
  不要输出markdown、代码块、工具回执或流程确认，
  只给用户真正需要听到的内容。

# ─── 语音上下文 ───
voice_context_max_turns: 6     # 保留对话轮数
voice_context_max_chars: 4000  # 上下文字符上限

# ─── Web 控制台 ───
console_host: "0.0.0.0"
console_port: 8199

# ─── 音频 ───
audio_public_base_url: ""      # 音箱可访问的服务器地址

# ─── 调试 ───
debug_log_enabled: true
```

---

## 13. 目录结构

```
~/xiaomi-llm-bridge/
├── main.py                    # 入口文件
├── config.yaml                # 配置文件（你创建的）
├── config.yaml.example        # 配置示例
├── requirements.txt           # Python 依赖
├── README.md                  # 项目说明
├── xiaomi-llm.service      # systemd 服务文件
│
├── xiaomi/                    # 小米 API 客户端
│   ├── __init__.py
│   ├── models.py              # 数据模型
│   ├── auth.py                # 账号登录认证
│   ├── mina.py                # MiNA API（设备控制、对话轮询）
│   └── miio.py                # MiIO API（MIoT 属性/动作）
│
├── bridge/                    # 桥接逻辑
│   ├── __init__.py
│   ├── client.py       # LLM API 客户端
│   └── poller.py              # 对话轮询器
│
└── console/                   # Web 控制台
    ├── __init__.py
    ├── app.py                 # FastAPI 后端
    └── templates/
        └── index.html         # 控制台前端
```

### 运行时数据目录

```
~/.xiaomi-llm-bridge/
├── config.yaml    # 持久化配置
├── tokens.json    # 小米登录态（自动生成）
├── device.json    # 已选设备信息（自动生成）
└── debug.log      # 调试日志（自动生成）
```

---

## 14. 故障排查

### 14.1 登录失败

**现象：** 控制台点击登录报错

**可能原因：**
1. 账号密码错误
2. 账号开启了安全验证（短信/邮箱验证码）
3. 小米服务器限制

**解决：**
- 查看服务端日志中的详细错误
- 如果需要安全验证，终端会打印验证 URL，需要手动处理
- 尝试在手机上先登录小米账号确认账号正常

### 14.2 设备列表为空

**现象：** 登录成功但看不到设备

**可能原因：**
1. 小爱音箱没有绑定到当前小米账号
2. MiNA API 获取设备列表失败

**解决：**
- 在米家 App 中确认音箱已绑定
- 检查网络连接

### 14.3 轮询无响应

**现象：** 启动后对小爱说话没有反应

**可能原因：**
1. 轮询接口对你的音箱型号不支持
2. 唤醒词没匹配（检查 wake_word_pattern）
3. 模式不对（确认不是 silent 模式）

**排查步骤：**
```bash
# 1. 确认服务在运行
# 看控制台是否显示"轮询: 🟢 运行中"

# 2. 查看详细日志
tail -f ~/.xiaomi-llm-bridge/debug.log

# 3. 对小爱说话，看日志中是否有：
#    ← [语音识别] "xxx"
# 如果没有，说明对话轮询接口可能不支持该型号

# 4. 切换到 proxy 模式测试
# 在控制台点"代理模式"再试
```

### 14.4 LLM API 不通

**现象：** 日志中出现 LLM API 错误

**排查：**
```bash
# 1. 确认 LLM 服务在运行
curl http://127.0.0.1:9222/v1/models

# 2. 确认端口正确
# config.yaml 中 api_url 是否正确
```

### 14.5 TTS 播报失败

**现象：** 日志显示回复成功但音箱没声音

**可能原因：**
1. 音箱不在线
2. 播报被小爱原生打断
3. MiNA API 调用失败

**排查：**
- 查看日志中 TTS 相关错误
- 在控制台手动点"播报"测试

---

## 15. 与原项目对比

| 特性 | Xiaoai-Claw-Addon (OpenClaw) | 本项目 (LLM) |
|------|------|------|
| 语言 | TypeScript | Python |
| 运行环境 | OpenClaw Gateway | 独立服务 |
| 小爱音箱Play增强版(L05C) | ✅ | ✅ (待验证) |
| 语音拦截 | ✅ | ✅ |
| 唤醒词拦截 | ✅ | ✅ |
| 代理模式 | ✅ | ✅ |
| 静默模式 | ✅ | ✅ |
| TTS 播报 | ✅ | ✅ |
| 音频URL播放 | ✅ | ✅ |
| 指令执行 | ✅ | ✅ |
| 音量控制 | ✅ | ✅ |
| MIoT Spec | ✅ | ✅ |
| Web 控制台 | ✅ (内嵌) | ✅ (独立) |
| 对话上下文 | ✅ | ✅ |
| 音频校准 | ✅ (复杂) | ❌ (简化) |
| 打断检测 | ✅ | ❌ (待实现) |
| 安装复杂度 | 高（需要 OpenClaw） | 低（pip install） |

---

## 16. 常见问题

### Q: 需要先安装 OpenClaw 吗？

A: 不需要。本项目是独立的 Python 服务，不依赖 OpenClaw。

### Q: 需要先启动 LLM 服务吗？

A: 需要。先在另一个终端运行 `hermes serve`，再启动桥接服务。

### Q: 对话会被小爱原生回答打断吗？

A: 在代理模式下，服务会在收到查询后先暂停小爱播放。但软件方案无法完全阻止，偶尔可能有重叠。

### Q: 可以同时控制多个小爱音箱吗？

A: 当前版本只支持同时控制一个音箱。如果需要多设备支持，可以运行多个实例。

### Q: 对话历史存在哪里？

A: 运行时的对话上下文在内存中，重启会清空。持久化的配置、登录态在 `~/.xiaomi-llm-bridge/` 目录下。

### Q: Token 会过期吗？

A: 会。小米的 serviceToken 有一定有效期。服务在 token 失效时会尝试自动刷新。如果刷新失败，需要重新登录。

### Q: 安全验证怎么处理？

A: 如果登录触发安全验证，服务日志会打印验证信息。可能需要：
1. 在手机上完成验证
2. 或在控制台中输入验证码

### Q: 代理模式下对小爱说"播放音乐"会怎样？

A: 在代理模式下，所有语音都被转发到 LLM，不会触发小爱原生的音乐播放。如果想让小爱播放音乐，用控制台的"执行"功能。

### Q: 如何更新？

```bash
cd ~/xiaomi-llm-bridge
git pull  # 如果是从 git 克隆的
pip3 install -r requirements.txt -U
sudo systemctl restart xiaomi-llm
```

---

## 快速开始 Checklist

- [ ] Python >= 3.10 已安装
- [ ] `pip3 install -r requirements.txt` 已执行
- [ ] `config.yaml` 已创建并配置
- [ ] LLM API Server 正在运行 (`hermes serve`)
- [ ] `python3 main.py` 已启动
- [ ] 浏览器打开 `http://服务器IP:8199`
- [ ] 小米账号已登录
- [ ] 小爱音箱已选择
- [ ] 轮询已启动
- [ ] 对小爱说"贾维斯你好"测试通过

---

*文档版本: 2026-05-09*
*项目: xiaomi-llm-bridge v1.0.0*
