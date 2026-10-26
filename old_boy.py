
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from slackeventsapi import SlackEventAdapter
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import logging
from flask import request, Response

# Configuration
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

signing_secret = os.getenv("SLACK_SIGNING_SECRET")
bot_token = os.getenv("SLACK_BOT_TOKEN")
fallback_channel = os.getenv("SLACK_CHANNEL", "").strip()

message_counts = {}
welcome_messages = {}

if not signing_secret:
    raise RuntimeError("Missing SLACK_SIGNING_SECRET in .env")

if not bot_token:
    raise RuntimeError("Missing SLACK_BOT_TOKEN in .env")

client = WebClient(token=bot_token)
BOT_ID = client.api_call("auth.test")['user_id']

# Keep this route consistent with Slack's Request URL.
slack_event_adapter = SlackEventAdapter(
    signing_secret,
    "/slack/event",
    app
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True
)

app.logger.setLevel(logging.DEBUG)

class WelcomeMessage:
    START_TEXT = {
        'type': 'section',
        'text': {
            'type': 'mrkdwn',
            'text': (
                'Welcome to this awesome channel! \n\n'
                '*Get started by completing the tasks!*'
            )
        }
    }

    DIVIDER = {'type': 'divider'}

    def __init__(self, channel):
        self.channel = channel
        self.icon_emoji = ':robot_face:'
        self.timestamp = ''
        self.completed = False

    def get_message(self):
        return {
            'ts': self.timestamp,
            'channel': self.channel,
            'username': 'Welcome Robot!',
            'icon_emoji': self.icon_emoji,
            'blocks': [
                self.START_TEXT,
                self.DIVIDER,
                self._get_reaction_task()
            ]
        }

    def _get_reaction_task(self):
        checkmark = ':white_check_mark:'
        if not self.completed:
            checkmark = ':white_square:'

        text = f'{checkmark} *React to this message!*'

        return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}


def send_welcome_message(channel, user):
    if channel not in welcome_messages:
        welcome_messages[channel] = {}

    if user in welcome_messages[channel]:
        return

    welcome = WelcomeMessage(channel)
    message = welcome.get_message()
    response = client.chat_postMessage(**message)
    welcome.timestamp = response['ts']

    welcome_messages[channel][user] = welcome


@app.before_request
def log_incoming_request():
    app.logger.info(
        "INCOMING REQUEST: %s %s",
        request.method,
        request.path
    )

@app.after_request
def log_response(response):
    app.logger.info(
        "OUTGOING RESPONSE: %s %s -> %s",
        request.method,
        request.path,
        response.status_code
    )
    return response

@app.errorhandler(Exception)
def log_unhandled_exception(error):
    app.logger.exception("UNHANDLED FLASK EXCEPTION")
    return {"error": "Internal server error"}, 500



@app.route("/health", methods=["GET"])
def health_check():
    return {"status": "ok"}, 200


@slack_event_adapter.on("error")
def handle_slack_error(error):
    logger.exception("Slack event adapter error: %s", error)


@app.route('/message-count', methods=['POST'])
def message_count():
    try:
        data = request.form
        user_id = data.get('user_id')
        channel_id = data.get('channel_id')
        message_count = message_counts.get(user_id, 0)
        client.chat_postMessage(channel=channel_id, text=f"Message count : {message_count}")
        return Response(), 200
    except SlackApiError as e:
        logger.error("Error fetching conversation history: %s", e.response['error'])
        return {"error": "Failed to fetch message count"}, 500

@slack_event_adapter.on("message")
def handle_message(event_data):
    try:
        event = event_data.get("event", {})

        # Ignore bot messages and message subtypes.
        if event.get("bot_id") or event.get("subtype"):
            return

        text = event.get("text", "").strip()
        channel = event.get("channel") or fallback_channel
        user_id = event.get("user")

        if user_id != None and BOT_ID != user_id:
            if user_id in message_counts:
                message_counts[user_id] += 1
            else:
                message_counts[user_id] = 1
        if text.lower() == 'start':
            send_welcome_message(channel,f'@{user_id}')

        if not text or not channel:
            logger.warning("Message ignored: missing text or channel")
            return

        logger.info(
            "Received message from user=%s in channel=%s: %s",
            user_id, channel, text
        )

        response = client.chat_postMessage(
            channel=channel,
            text=f"Received: {text}"
        )

        logger.info("Reply sent: %s", response["ok"])

    except SlackApiError as exc:
        logger.error(
            "Slack API error: %s",
            exc.response.get("error")
        )
    except Exception:
        logger.exception("Unexpected message-handler error")

@slack_event_adapter.on("reaction_added")
def reaction(payload):
    event = payload.get('event', {})
    channel_id = event.get('item', {}).get('channel')
    user_id = event.get('user')

    if f'@{user_id}' not in welcome_messages:
        return

    welcome = welcome_messages[f'@{user_id}'][user_id]
    welcome.completed = True
    welcome.channel = channel_id
    message = welcome.get_message()
    updated_message = client.chat_update(**message)
    welcome.timestamp = updated_message['ts']

if __name__ == "__main__":
    logger.info("Registered routes: %s", list(app.url_map.iter_rules()))
    app.run(
        host="0.0.0.0",
        port=3000,
        debug=False,
        use_reloader=False
    )