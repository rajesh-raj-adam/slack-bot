# Slack Bot Demo

A small Flask + Slack app that demonstrates how to build a friendly
Slack bot. It's intentionally small enough to read in one sitting,
but every feature is wired up the *correct* way (no known-bug patterns
from the tutorials).

### Features at a glance

| Feature | Trigger | What happens |
|--------|---------|---------------|
| 📨 **Echo** | Any message | Bot replies `Received: <your text>` |
| 🔢 **Counter** | Any message | Increments a per-user counter (in memory) |
| 👋 **Welcome** | Type `start` | Bot posts a checklist message |
| ✅ **React-to-complete** | React to that checklist | Checkbox flips from ☐ to ✅ |
| 🚫 **Bad words** | Message contains a flagged word | Bot replies in-thread `THAT IS A BAD WORD!` (no echo) |
| 🗓️ **Scheduled messages** | Opt-in demo | Schedules two messages 20s / 30s out, lists them, then cancels them (proves all three APIs work) |

---

## 1. What you need

| Requirement | Why |
|-------------|-----|
| Python **3.9+** | Uses `dict[str, int]` type hints |
| A **Slack workspace** where you can install an app | Test the bot |
| A **public URL** for Slack to reach your app | Slack pushes events to you |
| [ngrok](https://ngrok.com/) or [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) | Tunnel localhost → public URL |

---

## 2. Create the Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → *From scratch*.
2. Pick a name and the target workspace.

### 2.1 Add bot scopes

**OAuth & Permissions → Scopes → Bot Token Scopes**

| Scope | Used by |
|-------|---------|
| `chat:write` | Echo, welcome, bad-word reply, scheduled messages, `/message-count` |
| `channels:history` | Receive messages in public channels |
| `groups:history` | Receive messages in private channels |
| `im:history` | Receive DMs |
| `mpim:history` | Receive group DMs |
| `reactions:read` | Receive `reaction_added` |
| `commands` | Only if you wire up `/message-count` as a slash command |

> No extra scope is needed for scheduled messages — they ride on
> `chat:write`.

### 2.2 Enable events

**Event Subscriptions → Enable Events**

- **Request URL**: `https://<your-tunnel>/slack/event`
  (Slack sends a `url_verification` challenge — the app answers it for you.)
- **Subscribe to bot events**:
    - `message.channels`
    - `message.groups`
    - `message.im`
    - `message.mpim`
    - `reaction_added`

### 2.3 Install the app

**Install App → Install to Workspace** → copy the **Bot User OAuth Token**
(`xoxb-…`).

Also grab **Basic Information → App Credentials → Signing Secret**.

---

## 3. Configure `.env`

Copy the example below into a file named `.env` **next to `app.py`**:

```env
# Required
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_BOT_TOKEN=xoxb-your-bot-token

# Optional — used as a fallback if an event ever arrives without a channel
SLACK_CHANNEL=

# Optional — bad words (comma-separated, case-insensitive, word-boundary match)
BAD_WORDS=hmm,no,tim

# Optional — scheduled-message demo
DEMO_SCHEDULED_MESSAGES=false
DEMO_CHANNEL=C0123456789