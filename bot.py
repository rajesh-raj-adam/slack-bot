"""
Slack bot demo.

Features
--------
* Echo       : replies "Received: <text>" to every user message.
* Counter    : keeps an in-memory per-user message count.
* Welcome    : posts a checklist message when a user types "start".
* React     : updates that checklist message when the same user reacts to it.
* Bad words : replies "THAT IS A BAD WORD!" in a thread for flagged words.
* Scheduled : demo helper to schedule / list / delete Slack messages.

Run with `DEMO_SCHEDULED_MESSAGES=true` to also exercise the scheduled-
message demo at startup (it schedules two messages, lists them, then
cancels them — proving all three APIs work).
"""

# --- Standard library ---
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# --- Third party ---
from dotenv import load_dotenv
from flask import Flask, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slackeventsapi import SlackEventAdapter
from werkzeug.exceptions import HTTPException


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

# Canonical names (with backward-compatible fallback to the old ones)
SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET") or os.getenv("SIGNING_SECRET_")
BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_TOKEN_")
FALLBACK_CHANNEL = os.getenv("SLACK_CHANNEL", "").strip()

# Scheduled-message demo (opt-in, safe defaults)
DEMO_SCHEDULED = os.getenv("DEMO_SCHEDULED_MESSAGES", "false").lower() == "true"
DEMO_CHANNEL = os.getenv("DEMO_CHANNEL", FALLBACK_CHANNEL).strip()

# Bad-word list (word-boundary matching, case-insensitive)
BAD_WORDS = [
    w.strip().lower()
    for w in os.getenv("BAD_WORDS", "hmm,no,tim").split(",")
    if w.strip()
]
_BAD_WORD_PATTERN = (
    re.compile(r"\b(" + "|".join(re.escape(w) for w in BAD_WORDS) + r")\b", re.IGNORECASE)
    if BAD_WORDS
    else None
)

if not SIGNING_SECRET:
    logger.critical("Missing SLACK_SIGNING_SECRET in .env")
    raise RuntimeError("Missing SLACK_SIGNING_SECRET in .env")
if not BOT_TOKEN:
    logger.critical("Missing SLACK_BOT_TOKEN in .env")
    raise RuntimeError("Missing SLACK_BOT_TOKEN in .env")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Slack client
# ---------------------------------------------------------------------------
client = WebClient(token=BOT_TOKEN)

try:
    BOT_ID = client.auth_test()["user_id"]
    logger.info("Authenticated with Slack as bot user %s", BOT_ID)
except SlackApiError as exc:
    reason = exc.response.get("error", "unknown error")
    logger.critical("Slack auth.test failed: %s", reason)
    raise RuntimeError(f"Slack authentication failed: {reason}") from exc

slack_event_adapter = SlackEventAdapter(SIGNING_SECRET, "/slack/event", app)


# ---------------------------------------------------------------------------
# In-memory state (add a real DB for production)
# ---------------------------------------------------------------------------
state_lock = threading.Lock()
message_counts: dict[str, int] = {}
# structure: welcome_messages[channel_id][f"@user_id"] -> WelcomeMessage
welcome_messages: dict[str, dict[str, "WelcomeMessage"]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def contains_bad_word(text: Optional[str]) -> bool:
    """Word-boundary match so 'no' does not fire on 'not', 'now', etc."""
    if not text or _BAD_WORD_PATTERN is None:
        return False
    return bool(_BAD_WORD_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Welcome message
# ---------------------------------------------------------------------------
class WelcomeMessage:
    """A checklist-style welcome message tracked per (channel, user)."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.timestamp = ""
        self.completed = False

    def _blocks(self) -> list:
        checkmark = ":white_check_mark:" if self.completed else ":white_large_square:"
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Welcome to this awesome channel! \n\n"
                        "*Get started by completing the tasks!*"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{checkmark} *React to this message!*",
                },
            },
        ]

    def post_payload(self) -> dict:
        return {
            "channel": self.channel,
            "text": "Welcome! React to this message to finish the task.",
            "blocks": self._blocks(),
        }

    def update_payload(self) -> dict:
        return {
            "channel": self.channel,
            "ts": self.timestamp,
            "text": "Welcome! React to this message to finish the task.",
            "blocks": self._blocks(),
        }


def send_welcome_message(channel: str, user_key: str) -> None:
    """Post a welcome message once per (channel, user_key)."""
    if not channel or not user_key:
        return

    with state_lock:
        channel_store = welcome_messages.setdefault(channel, {})
        if user_key in channel_store:
            return

    welcome = WelcomeMessage(channel)
    try:
        response = client.chat_postMessage(**welcome.post_payload())
    except SlackApiError:
        logger.exception("Failed to send welcome message to %s", channel)
        return

    welcome.timestamp = response["ts"]
    with state_lock:
        welcome_messages[channel][user_key] = welcome


# ---------------------------------------------------------------------------
# Scheduled messages
# ---------------------------------------------------------------------------
def schedule_message(channel: str, text: str, post_at: int) -> Optional[str]:
    try:
        resp = client.chat_scheduleMessage(
            channel=channel, text=text, post_at=post_at
        )
        sid = resp.get("scheduled_message_id")
        logger.info("Scheduled message %s in %s for %s", sid, channel, post_at)
        return sid
    except SlackApiError:
        logger.exception("Failed to schedule message in %s", channel)
        return None


def schedule_messages(messages: list) -> list:
    ids = []
    for msg in messages:
        sid = schedule_message(msg["channel"], msg["text"], msg["post_at"])
        if sid:
            ids.append(sid)
    return ids


def list_scheduled_message_ids(channel: str) -> list:
    try:
        resp = client.chat_scheduledMessages_list(channel=channel)
        return [m.get("id") for m in resp.data.get("scheduled_messages", [])]
    except SlackApiError:
        logger.exception("Failed to list scheduled messages in %s", channel)
        return []


def delete_scheduled_message(channel: str, scheduled_message_id: str) -> None:
    try:
        client.chat_deleteScheduledMessage(
            channel=channel, scheduled_message_id=scheduled_message_id
        )
        logger.info("Deleted scheduled message %s", scheduled_message_id)
    except SlackApiError:
        logger.exception("Failed to delete scheduled message %s", scheduled_message_id)


def delete_scheduled_messages(channel: str, ids: list) -> None:
    for sid in ids:
        delete_scheduled_message(channel, sid)


def run_scheduled_message_demo(channel: str) -> None:
    """Schedule → list → delete — proves all three APIs work end-to-end."""
    logger.info("Scheduled-message demo starting in %s", channel)
    demo = [
        {
            "channel": channel,
            "text": "First message",
            "post_at": int((datetime.now() + timedelta(seconds=20)).timestamp()),
        },
        {
            "channel": channel,
            "text": "Second message!",
            "post_at": int((datetime.now() + timedelta(seconds=30)).timestamp()),
        },
    ]
    scheduled_ids = schedule_messages(demo)
    listed_ids = list_scheduled_message_ids(channel)
    logger.info("Scheduled IDs: %s | Listed IDs: %s", scheduled_ids, listed_ids)
    delete_scheduled_messages(channel, scheduled_ids)
    logger.info("Scheduled-message demo finished (messages cancelled)")


# ---------------------------------------------------------------------------
# Flask request logging / errors
# ---------------------------------------------------------------------------
@app.before_request
def log_incoming_request() -> None:
    app.logger.info("INCOMING: %s %s", request.method, request.path)


@app.after_request
def log_response(response):
    app.logger.info(
        "OUTGOING: %s %s -> %s", request.method, request.path, response.status_code
    )
    return response


@app.errorhandler(Exception)
def log_unhandled_exception(error):
    if isinstance(error, HTTPException):
        return error
    app.logger.exception("UNHANDLED FLASK EXCEPTION")
    return {"error": "Internal server error"}, 500


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health_check():
    return {"status": "ok"}, 200


@app.route("/message-count", methods=["POST"])
def message_count():
    """Slash-command endpoint: post a user's message count into the channel."""
    data = request.form
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")

    if not channel_id:
        return {"error": "Missing channel_id"}, 400

    with state_lock:
        count = message_counts.get(user_id, 0)

    try:
        client.chat_postMessage(channel=channel_id, text=f"Message: {count}")
    except SlackApiError:
        logger.exception("Failed to post message count to %s", channel_id)
        return {"error": "Failed to post message count"}, 500

    return "", 200


# ---------------------------------------------------------------------------
# Slack events
# ---------------------------------------------------------------------------
@slack_event_adapter.on("error")
def handle_slack_error(error) -> None:
    logger.error("Slack event adapter error: %s", error)


@slack_event_adapter.on("message")
def handle_message(event_data: dict) -> None:
    try:
        event = event_data.get("event", {})

        if event.get("bot_id") or event.get("subtype"):
            return

        user_id = event.get("user")
        text = (event.get("text") or "").strip()
        channel = event.get("channel") or FALLBACK_CHANNEL
        ts = event.get("ts")

        # Counter (skip system messages and the bot itself)
        if user_id and user_id != BOT_ID:
            with state_lock:
                message_counts[user_id] = message_counts.get(user_id, 0) + 1

        if not channel:
            logger.warning("Message ignored: missing channel")
            return
        if not text:
            logger.debug("Message ignored: empty text in %s", channel)
            return

        logger.info(
            "Received message from user=%s in channel=%s: %s",
            user_id, channel, text,
        )

        # "start" → welcome message
        if text.lower() == "start" and user_id:
            send_welcome_message(channel, f"@{user_id}")

        # Bad word → threaded warning, skip the echo
        if contains_bad_word(text):
            client.chat_postMessage(
                channel=channel,
                thread_ts=ts,
                text="THAT IS A BAD WORD!",
            )
            return

        # Default: echo
        response = client.chat_postMessage(
            channel=channel, text=f"Received: {text}"
        )
        logger.info("Reply sent: %s", response.get("ok"))

    except SlackApiError as exc:
        logger.error("Slack API error: %s", exc.response.get("error"))
    except Exception:
        logger.exception("Unexpected message-handler error")


@slack_event_adapter.on("reaction_added")
def handle_reaction(payload: dict) -> None:
    event = payload.get("event", {})
    user_id = event.get("user")
    item = event.get("item", {})
    channel_id = item.get("channel")
    item_ts = item.get("ts")

    if not user_id or not channel_id or item.get("type") != "message":
        return

    user_key = f"@{user_id}"
    with state_lock:
        welcome = welcome_messages.get(channel_id, {}).get(user_key)

    if not welcome:
        logger.info("Reaction ignored: no welcome for %s in %s", user_key, channel_id)
        return
    if item_ts != welcome.timestamp:
        logger.info("Reaction ignored: ts mismatch for %s in %s", user_key, channel_id)
        return

    welcome.completed = True
    try:
        updated = client.chat_update(**welcome.update_payload())
        welcome.timestamp = updated["ts"]
        logger.info("Welcome completed for %s in %s", user_key, channel_id)
    except SlackApiError:
        logger.exception("Failed to update welcome message")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Registered routes: %s", list(app.url_map.iter_rules()))

    if DEMO_SCHEDULED:
        if not DEMO_CHANNEL:
            logger.warning(
                "DEMO_SCHEDULED_MESSAGES=true but neither DEMO_CHANNEL nor "
                "SLACK_CHANNEL is set — skipping demo."
            )
        else:
            run_scheduled_message_demo(DEMO_CHANNEL)

    app.run(host="0.0.0.0", port=3000, debug=False, use_reloader=False)