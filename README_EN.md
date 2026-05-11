# XiaoAi-Hermes Bridge (migpt-hermes)

> Connect your Xiaomi XiaoAi smart speaker to Hermes Agent — speak to XiaoAi, Hermes answers, XiaoAi announces.

[中文文档](README.md)

## Features

- 🎙️ Voice conversation polling → forward to LLM → TTS playback
- 🔊 Remote announcement, audio playback, command execution, volume control
- 🎯 Wake word interception / Proxy mode / Silent mode
- 🌐 Web console for management
- 💾 Persistent configuration and login state
- 🆔 Identity post-processing — fixes LLM model identity bias (e.g., mimo series)
- 📅 Date context injection — automatically injects current date into conversations
- 🔌 MIoT TTS protocol — supports TTS for L05C and other MIoT devices
- 🔄 Connection pool self-healing — auto-rebuilds HTTP pool after consecutive failures

## Quick Start

### 1. Install Dependencies

```bash
git clone https://github.com/tengxunlaozu/migpt-hermes.git
cd migpt-hermes
pip3 install -r requirements.txt
```

### 2. Configure

```bash
cp config.yaml.example config.yaml
nano config.yaml  # Edit LLM API URL and Key
```

**Required settings:**

```yaml
hermes_api_url: "https://your-llm-api-url"
hermes_api_key: "your-api-key"
hermes_model: "model-name"
```

### 3. Start

```bash
python3 main.py
```

### 4. Login via Browser

Open `http://your-server-ip:8199`

**Set up Xiaomi credentials (required on first run):**

1. Click the **"Open Xiaomi Login Page"** link on the console
2. Log in with your Xiaomi account
3. After login, press **F12** → **Application** → **Cookies** → **account.xiaomi.com**
4. Copy **userId** (numeric) and **passToken** (string starting with `V1:`)
5. Paste into the login form and click **"Save & Login"**

### 5. Select Device & Start

After login:
1. Select your XiaoAi speaker from the device list
2. Click **"▶ Start"** to begin polling

### 6. Test

Say to your speaker: **"小爱同学，贾维斯，你好"** (XiaoAi, Jarvis, hello)

> You must say "小爱同学" (XiaoAi) to wake the speaker first, then the bridge will detect subsequent speech.

## Usage

### Daily Use

```
You say: "小爱同学，贾维斯，今天天气怎么样？"
              ↓
Speaker recognizes speech → uploads to Xiaomi cloud
              ↓
Bridge polls → matches "贾维斯" → forwards to LLM
              ↓
LLM responds → XiaoAi announces
```

### Voice Modes

| Mode | Description | Normal Device Control |
|------|-------------|----------------------|
| **wake** (default) | Only intercepts conversations matching wake word | ✅ Unaffected |
| **proxy** | Intercepts all conversations | ❌ Gets intercepted |
| **silent** | No interception, active announcements only | ✅ Unaffected |

### Wake Words

Default: matches `贾维斯` or `jarvis` (case-insensitive). Customize in `config.yaml`:

```yaml
wake_word_pattern: "(贾维斯|jarvis|Jarvis|JARVIS)"
```

### Exit Conversation

Say any of these keywords to close the dialog window and return to normal mode:

> 退出 (exit), 再见 (goodbye), 拜拜 (bye), 关闭 (close), 关机 (shutdown), 停止 (stop)

### Web Console

| Feature | Action |
|---------|--------|
| Login | Enter userId + passToken |
| Start/Stop | Click button |
| Switch Mode | Wake/Proxy/Silent |
| Announce | Type text and send |
| Execute Command | Enter XiaoAi command |
| Adjust Volume | Enter 0-100 |

## Configuration Reference

All settings are documented with comments. See [config.yaml.example](config.yaml.example).

Key settings:

| Setting | Description | Default |
|---------|-------------|---------|
| `hermes_api_url` | LLM API URL | `https://token-plan-cn.xiaomimimo.com` |
| `hermes_api_key` | API Key | - |
| `hermes_model` | Model name | `mimo-v2.5` |
| `mode` | Voice mode | `wake` |
| `wake_word_pattern` | Wake word regex | `(贾维斯\|jarvis\|Jarvis\|JARVIS)` |
| `console_port` | Console port | `8199` |
| `hermes_system_prompt` | System prompt | Brief spoken-answer style |

## systemd Service

```bash
sudo cp migpt-hermes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable migpt-hermes
sudo systemctl start migpt-hermes
sudo journalctl -u migpt-hermes -f  # View logs
```

## Changelog

### v2.1.0

**New Features:**
- **Identity post-processing** (`_fix_identity`): Automatically replaces "小爱" (XiaoAi) with "贾维斯" (Jarvis) in LLM responses, handling edge cases like negative sentences. Designed for models with strong identity priors (e.g., mimo series).
- **Date context injection**: Injects current date/time into the system prompt on every request, fixing incorrect date responses from LLMs.
- **Exit keywords**: Say "退出/再见/拜拜/关闭/关机/停止" to close the dialog window.
- **MIoT TTS**: Supports TTS via MIoT protocol for devices like L05C that don't support ubus.
- **Post-TTS wake-up**: Automatically re-activates the speaker after TTS playback to maintain listening state.
- **TTS text cleanup**: Strips newlines, markdown, and emoji for cleaner speech output.

**Improvements:**
- **RC4 encrypted signing**: MiIO API now uses the micloud library's RC4 encryption for better compatibility.
- **Connection pool self-healing**: Auto-rebuilds HTTP connection pool after 3 consecutive polling failures.
- **IPv4 enforcement**: Avoids IPv6 connection timeout issues.
- **Response parsing**: Fixes double-encoded JSON parsing in MiNA API responses.
- **Wake word matching**: Changed from "match at start only" to "match anywhere in query".
- **Nested TTS parsing**: Fixes parsing of nested dict TTS format from some devices.

### v2.0.0
- Browser-based credential login (passToken)
- Web console redesign
- MiNA API integration

### v1.0.0
- Initial release
- Voice polling and LLM bridge
- Basic TTS playback

## FAQ

**Q: Does passToken expire?**
A: Yes. You'll need to re-obtain it from the browser. Typically valid for a few days to weeks. Symptoms: polling returns empty records, speaker doesn't respond.

**Q: Can a cloud server control my home speaker?**
A: Yes. Communication goes through Xiaomi's cloud API — no need to be on the same local network.

**Q: Will "小爱同学, turn on the lamp" be intercepted?**
A: No, in wake mode only messages containing "贾维斯" (Jarvis) are intercepted.

**Q: Where is conversation history stored?**
A: In memory at runtime (cleared on restart). Configuration and login state are in `~/.xiaomi-hermes-bridge/`.

**Q: Does L05C work?**
A: Yes. v2.1.0 added MIoT TTS protocol support for L05C and similar devices.

## Project Structure

```
migpt-hermes/
├── main.py                 # Entry point
├── config.yaml.example     # Config template
├── requirements.txt        # Dependencies
├── migpt-hermes.service    # systemd service
├── xiaomi/                 # Xiaomi API clients
│   ├── models.py           # Data models
│   ├── auth.py             # Login authentication
│   ├── auth_portal.py      # Browser credential login
│   ├── mina.py             # MiNA API (device control, dialog polling)
│   └── miio.py             # MiIO API (MIoT props/actions, RC4 encryption)
├── bridge/                 # Bridge logic
│   ├── hermes_client.py    # LLM API client (identity fix, TTS cleanup)
│   └── poller.py           # Dialog poller (exit words, MIoT TTS, wake)
└── console/                # Web console
    ├── app.py              # FastAPI backend
    └── templates/
        └── index.html      # Frontend
```

## Runtime Data

```
~/.xiaomi-hermes-bridge/
├── config.yaml    # Persistent config
├── tokens.json    # Xiaomi login state (auto-generated)
├── device.json    # Selected device (auto-generated)
└── debug.log      # Debug log
```

## License

MIT License
