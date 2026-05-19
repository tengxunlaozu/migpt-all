# 小爱-Hermes 桥接服务 (migpt-hermes)

> 把小爱音箱接入 Hermes Agent —— 对小爱说话，Hermes 回答，小爱播报。
> **[English Version](README_EN.md)**
## 功能

- 🎙️ 语音对话轮询 → 转发到 LLM → TTS 回播
- 🔊 远程播报、播放音频、执行指令、调节音量
- 🎯 唤醒词拦截 / 代理模式 / 静默模式
- 🌐 Web 控制台管理
- 💾 配置与登录态持久化
- 🆔 身份后处理 — 修复 LLM 模型的身份先验（如 mimo 系列）
- 📅 日期上下文注入 — 自动将当前日期注入对话，避免 LLM 回答日期错误
- 🔌 MIoT TTS 协议 — 支持 L05C 等 MIoT 设备的 TTS 播报
- 🔄 连接池自愈 — 连续失败自动重建 HTTP 连接池
- 🔐 Token 自动刷新 — passToken 过期时自动尝试续期，减少手动操作
- 🤖 大模型热配置 — 控制台直接修改 LLM API 地址/模型/密钥，即时生效

## 快速开始

### 1. 安装依赖

```bash
git clone https://github.com/tengxunlaozu/migpt-hermes.git
cd migpt-hermes
pip3 install -r requirements.txt
```

### 2. 配置

```bash
cp config.yaml.example config.yaml
nano config.yaml  # 编辑 LLM API 地址和 Key
```

**必填配置项：**

```yaml
hermes_api_url: "https://你的LLM-API地址"
hermes_api_key: "你的API密钥"
hermes_model: "模型名"
```

### 3. 启动

```bash
python3 main.py
```

### 4. 浏览器登录小米账号

打开 `http://你的服务器IP:8199`

**配置小米凭证（首次必须）：**

1. 在控制台页面点 **"打开小米登录页"** 链接
2. 用你的小米账号密码登录
3. 登录成功后，按 **F12** → **Application** → **Cookies** → **account.xiaomi.com**
4. 复制 **userId**（数字）和 **passToken**（`V1:` 开头的长字符串）
5. 粘贴到控制台的登录表单中，点 **"保存并登录"**

### 5. 选择设备 & 启动

登录成功后：
1. 在设备列表中选择你的小爱音箱
2. 点 **"▶ 启动"** 开始轮询

### 6. 测试

对小爱说：**"小爱同学，贾维斯，你好"**

> 必须先说"小爱同学"唤醒音箱，然后说的内容才会被桥接服务检测。

## 使用说明

### 日常使用

```
你说: "小爱同学，贾维斯，今天天气怎么样？"
              ↓
音箱识别语音 → 上传到小米云端
              ↓
桥接服务轮询到 → 匹配"贾维斯" → 转发给 LLM
              ↓
LLM 回复 → 小爱播报
```

### 语音模式

| 模式 | 说明 | 日常设备控制 |
|------|------|------|
| **wake**（默认） | 只拦截匹配唤醒词的对话 | ✅ 不受影响 |
| **proxy** | 拦截所有对话 | ❌ 会被拦截 |
| **silent** | 不拦截，只保留主动播报 | ✅ 不受影响 |

### 唤醒词

默认匹配 `贾维斯`、`jarvis`（不区分大小写），可在 `config.yaml` 中修改：

```yaml
wake_word_pattern: "(贾维斯|jarvis|Jarvis|JARVIS)"
```

### 退出对话

在对话窗口中说以下关键词可关闭对话窗口，回到正常模式：

> 退出、再见、拜拜、关闭、关机、停止

### Web 控制台

| 功能 | 操作 |
|------|------|
| 登录 | 填入 userId + passToken |
| 启动/停止 | 点按钮 |
| 切换模式 | 唤醒/代理/静默 |
| 播报 | 输入文字点发送 |
| 执行指令 | 输入小爱指令 |
| 调音量 | 输入 0-100 或点"读取"查看当前音量 |
| 大模型配置 | 查看/修改 API 地址、模型、密钥（即时生效+持久化） |

## 配置文件参考

所有配置项都有注释，详见 [config.yaml.example](config.yaml.example)

主要配置项：

| 配置 | 说明 | 默认值 |
|------|------|--------|
| `hermes_api_url` | LLM API 地址 | `https://token-plan-cn.xiaomimimo.com` |
| `hermes_api_key` | API Key | - |
| `hermes_model` | 模型名 | `mimo-v2.5` |
| `mode` | 语音模式 | `wake` |
| `wake_word_pattern` | 唤醒词正则 | `(贾维斯\|jarvis\|Jarvis\|JARVIS)` |
| `console_port` | 控制台端口 | `8199` |
| `hermes_system_prompt` | 系统提示词 | 简短口头回答风格 |

## systemd 服务

```bash
sudo cp migpt-hermes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable migpt-hermes
sudo systemctl start migpt-hermes
sudo journalctl -u migpt-hermes -f  # 查看日志
```

## v2.2.1 更新日志

### 新特性
- **Token 自动刷新**: 连续 401 达到阈值后，自动用 passToken 刷新 micoapi serviceToken，无需手动操作
- **Token 过期检测**: 轮询前检查 token 状态，过期后自动进入退避等待，避免疯狂重试
- **大模型热配置**: 控制台新增"大模型配置"面板，可查看/修改 API 地址、模型名称、API 密钥，运行时即时生效并自动持久化
- **音量读取**: 新增 `GET /api/volume` 接口，支持读取音箱当前音量
- **客户端引用修复**: 登录后自动更新 poller 的 mina/miio 客户端引用，注册 token 刷新回调

### 改进
- **MIoT TTS 防御**: serviceToken 为空时跳过 MIoT TTS 和唤醒，避免无效请求
- **轮询异常处理**: passToken 过期相关异常进入 60 秒长等待，给用户时间重新登录
- **音量设置反馈**: 设置音量后前端显示确认通知

## v2.2.0 更新日志

- **控制台安全修复**: 移除控制台 placeholder 中的真实 userId
- **英文版文档**: 新增 README_EN.md

## v2.1.0 更新日志

### 新特性
- **身份后处理** (`_fix_identity`): 自动将 LLM 回复中的"小爱"替换为"贾维斯"，处理否定句等边界情况（适用于 mimo 等有身份先验的模型）
- **日期上下文注入**: 每次请求自动将当前日期时间注入 system prompt，解决 LLM 回答日期错误的问题
- **退出关键词**: 说"退出/再见/拜拜/关闭/关机/停止"可关闭对话窗口
- **MIoT TTS**: 支持通过 MIoT 协议发送 TTS，兼容 L05C 等不支持 ubus 的设备
- **TTS 后唤醒**: 播报完成后自动重新唤醒音箱，保持监听状态
- **TTS 文本清理**: 自动去除换行、markdown 标记、emoji，提升播报质量

### 改进
- **RC4 加密签名**: MiIO API 改用 micloud 库的 RC4 加密，提升兼容性
- **连接池自愈**: 连续 3 次轮询失败后自动重建 HTTP 连接池，解决僵尸连接问题
- **IPv4 强制**: 避免 IPv6 连接超时问题
- **响应解析**: 修复 MiNA API 双重编码 JSON 的解析问题
- **唤醒词匹配**: 从"仅匹配开头"改为"匹配任意位置"，支持在对话中间插入唤醒词
- **嵌套 TTS 解析**: 修复某些设备返回嵌套 dict 格式 TTS 的解析

## 常见问题

**Q: passToken 会过期吗？**
A: 会。v2.2.1 起支持自动刷新 —— 检测到连续 401 后会自动用 passToken 续期 serviceToken。但 passToken 本身也有有效期（通常几天到几周），过期后仍需手动从浏览器重新获取。症状：控制台提示"passToken 已过期"。可以通过控制台的大模型配置面板查看状态。

**Q: 云服务器能控制家里的音箱吗？**
A: 能。通过小米云端 API 中转，不需要在同一局域网。

**Q: 说"小爱同学，打开台灯"会被拦截吗？**
A: 默认 wake 模式不会。只有包含"贾维斯"的才会被拦截。

**Q: 对话历史存在哪？**
A: 运行时在内存中（重启清空）。配置和登录态在 `~/.xiaomi-hermes-bridge/`。

**Q: L05C 设备能用吗？**
A: 能。v2.1.0 新增了 MIoT TTS 协议支持，L05C 等设备优先使用 MIoT 播报。

## 目录结构

```
migpt-hermes/
├── main.py                 # 入口
├── config.yaml.example     # 配置模板
├── requirements.txt        # 依赖
├── migpt-hermes.service    # systemd 服务
├── xiaomi/                 # 小米 API 客户端
│   ├── models.py           # 数据模型
│   ├── auth.py             # 登录认证
│   ├── auth_portal.py      # 浏览器凭证登录
│   ├── mina.py             # MiNA API（设备控制、对话轮询、Token 自动刷新）
│   └── miio.py             # MiIO API（MIoT 属性/动作，RC4 加密）
├── bridge/                 # 桥接逻辑
│   ├── hermes_client.py    # LLM API 客户端（身份修复、TTS 清理）
│   └── poller.py           # 对话轮询器（退出词、MIoT TTS、唤醒）
└── console/                # Web 控制台
    ├── app.py              # FastAPI 后端
    └── templates/
        └── index.html      # 前端
```

## 运行时数据

```
~/.xiaomi-hermes-bridge/
├── config.yaml    # 持久化配置
├── tokens.json    # 小米登录态（自动生成）
├── device.json    # 已选设备（自动生成）
└── debug.log      # 调试日志
```

## 许可证

MIT License
