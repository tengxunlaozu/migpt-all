# 小爱-Hermes 桥接服务 (migpt-hermes)

> 把小爱音箱接入 Hermes Agent —— 对小爱说话，Hermes 回答，小爱播报。

## 功能

- 🎙️ 语音对话轮询 → 转发到 LLM → TTS 回播
- 🔊 远程播报、播放音频、执行指令、调节音量
- 🎯 唤醒词拦截 / 代理模式 / 静默模式
- 🌐 Web 控制台管理
- 💾 配置与登录态持久化

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

默认匹配 `^(贾维斯| Jarvis)`，可在 `config.yaml` 中修改：

```yaml
wake_word_pattern: "^(贾维斯| Jarvis)"
```

### Web 控制台

| 功能 | 操作 |
|------|------|
| 登录 | 填入 userId + passToken |
| 启动/停止 | 点按钮 |
| 切换模式 | 唤醒/代理/静默 |
| 播报 | 输入文字点发送 |
| 执行指令 | 输入小爱指令 |
| 调音量 | 输入 0-100 |

## 配置文件参考

所有配置项都有注释，详见 [config.yaml.example](config.yaml.example)

主要配置项：

| 配置 | 说明 | 默认值 |
|------|------|--------|
| `hermes_api_url` | LLM API 地址 | `https://token-plan-cn.xiaomimimo.com` |
| `hermes_api_key` | API Key | - |
| `hermes_model` | 模型名 | `mimo-v2.5` |
| `mode` | 语音模式 | `wake` |
| `wake_word_pattern` | 唤醒词正则 | `^(贾维斯\| Jarvis)` |
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

## 常见问题

**Q: passToken 会过期吗？**
A: 会。过期后需要重新从浏览器获取。通常有效期几天到几周。

**Q: 云服务器能控制家里的音箱吗？**
A: 能。通过小米云端 API 中转，不需要在同一局域网。

**Q: 说"小爱同学，打开台灯"会被拦截吗？**
A: 默认 wake 模式不会。只有"贾维斯"开头的才会被拦截。

**Q: 对话历史存在哪？**
A: 运行时在内存中（重启清空）。配置和登录态在 `~/.xiaomi-hermes-bridge/`。

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
│   ├── mina.py             # MiNA API（设备控制、对话轮询）
│   └── miio.py             # MiIO API（MIoT 属性/动作）
├── bridge/                 # 桥接逻辑
│   ├── hermes_client.py    # LLM API 客户端
│   └── poller.py           # 对话轮询器
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
