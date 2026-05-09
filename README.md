# 小爱-Hermes 桥接服务 (XiaoAi-Hermes Bridge)

把小爱音箱接进 Hermes Agent，让小爱成为你的语音 Agent 入口。

功能对标 [Xiaoai-Claw-Addon](https://github.com/ZhengXieGang/Xiaoai-Claw-Addon)，但使用 Python 实现，适配 Hermes Agent。

## 功能

- 🎙️ **语音对话** — 对小爱说话 → Hermes 回答 → 小爱播报
- 🔊 **TTS 播报** — 任意文本让小爱说出来
- 🎵 **音频播放** — 让小爱播放 URL 音频
- ⚡ **指令执行** — 远程执行小爱语音指令
- 🔇 **音量控制** — 远程调节音量
- 🎯 **唤醒词拦截** — 只在检测到唤醒词时才转发
- 🔄 **代理模式** — 拦截所有语音
- ⏸️ **静默模式** — 只保留主动播报
- 🌐 **Web 控制台** — 浏览器管理界面
- 💾 **持久化配置** — 登录态、设备、配置自动保存
- 📋 **调试日志** — 详细运行日志

## 快速开始

```bash
# 1. 安装依赖
cd ~/xiaomi-hermes-bridge
pip3 install -r requirements.txt

# 2. 复制配置
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入你的 Hermes API 地址

# 3. 启动
python3 main.py

# 4. 打开控制台
# 浏览器访问 http://your-server:8199
# 登录小米账号 → 选择音箱 → 启动
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `XIAOMI_ACCOUNT` | 小米账号 |
| `XIAOMI_PASSWORD` | 小米密码 |
| `HERMES_API_URL` | Hermes API 地址 (默认 http://127.0.0.1:9222) |
| `HERMES_API_KEY` | Hermes API Key |
| `HERMES_MODEL` | 模型名 (默认 mimo-v2.5) |

## 命令行参数

```bash
python3 main.py                      # 正常启动
python3 main.py --config path.yaml   # 指定配置
python3 main.py --console-only       # 只启动控制台
python3 main.py --port 8080          # 指定端口
python3 main.py --verbose            # 详细日志
```

## 作为服务运行

```bash
# 复制 systemd 文件
sudo cp xiaomi-hermes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xiaomi-hermes
sudo systemctl start xiaomi-hermes

# 查看日志
sudo journalctl -u xiaomi-hermes -f
```

## 语音模式

| 模式 | 说明 |
|------|------|
| `wake` | 唤醒词模式 — 只在检测到唤醒词（如"贾维斯"）时转发 |
| `proxy` | 代理模式 — 拦截所有小爱语音对话 |
| `silent` | 静默模式 — 不拦截，只保留主动播报 |

## 与 xiaogpt 的区别

| 特性 | 本项目 | xiaogpt |
|------|--------|---------|
| 小爱音箱Play增强版(L05C) | ✅ 支持 | ❌ 不支持 |
| 唤醒词拦截 | ✅ | ❌ |
| Web 控制台 | ✅ | ❌ |
| 对话上下文记忆 | ✅ | ✅ |
| 音频 URL 播放 | ✅ | ✅ |
| 代理模式 | ✅ | ✅ |
| MIoT Spec 自动探测 | ✅ | ❌ |

## 目录结构

```
xiaomi-hermes-bridge/
├── main.py                 # 入口
├── xiaomi/
│   ├── models.py           # 数据模型
│   ├── auth.py             # 登录认证
│   ├── mina.py             # MiNA API (设备控制、对话轮询)
│   └── miio.py             # MiIO API (MIoT 属性/动作)
├── bridge/
│   ├── hermes_client.py    # Hermes API 客户端
│   └── poller.py           # 对话轮询器
├── console/
│   ├── app.py              # Web 控制台后端
│   └── templates/
│       └── index.html      # 控制台前端
├── config.yaml.example     # 配置示例
└── requirements.txt        # 依赖
```

## 数据存储

所有配置和状态保存在 `~/.xiaomi-hermes-bridge/`：

```
~/.xiaomi-hermes-bridge/
├── config.yaml    # 配置
├── tokens.json    # 小米登录态
├── device.json    # 已选设备
└── debug.log      # 调试日志
```

## 许可证

MIT License
