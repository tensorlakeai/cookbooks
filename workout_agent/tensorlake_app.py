"""Tensorlake application entry points for the workout coach agent."""

import asyncio
import base64
import os

import httpx
from pydantic import BaseModel, Field
from tensorlake.applications import Image, Request, application, function, run_local_application
from twilio.rest import Client

from agent import run_agent
import db

# Image with all required dependencies
workout_agent_image = (
    Image()
    .run("pip install openai-agents asyncpg pydantic python-dotenv twilio httpx")
)


class MessageRequest(BaseModel):
    user_id: str
    message: str


class MediaItem(BaseModel):
    url: str
    contentType: str


class TwilioWebhook(BaseModel):
    messageSid: str
    smsSid: str
    accountSid: str
    messagingServiceSid: str
    from_: str = Field(alias="from")  # Sender's phone number (becomes user_id)
    to: str                           # The Twilio number (used for reply)
    body: str                         # Message text
    numMedia: str = "0"
    receivedAt: str
    media: list[MediaItem] = []


@application()
@function(image=workout_agent_image, secrets=["OPENAI_API_KEY", "DATABASE_URL_WORKOUT_APP"], min_containers=2)
def handle_message(request: MessageRequest) -> str:
    """Handle a user message and return the coach's response."""

    async def _run():
        await db.init_db()
        return await run_agent(request.user_id, request.message)

    return asyncio.run(_run())


SMS_MAX_LEN = 1600


def _split_sms(text: str, max_len: int = SMS_MAX_LEN) -> list[str]:
    """Split text into SMS-sized chunks, preferring paragraph then sentence boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Try to split on a paragraph boundary (\n\n)
        cut = text.rfind("\n\n", 0, max_len)
        if cut > 0:
            chunks.append(text[:cut])
            text = text[cut + 2:]
            continue

        # Try a single newline
        cut = text.rfind("\n", 0, max_len)
        if cut > 0:
            chunks.append(text[:cut])
            text = text[cut + 1:]
            continue

        # Try a sentence boundary (. ! ?)
        for sep in (". ", "! ", "? "):
            cut = text.rfind(sep, 0, max_len)
            if cut > 0:
                chunks.append(text[:cut + 1])
                text = text[cut + 2:]
                break
        else:
            # Last resort: split on space
            cut = text.rfind(" ", 0, max_len)
            if cut > 0:
                chunks.append(text[:cut])
                text = text[cut + 1:]
            else:
                chunks.append(text[:max_len])
                text = text[max_len:]

    return chunks


@application()
@function(
    image=workout_agent_image,
    secrets=["OPENAI_API_KEY", "DATABASE_URL_WORKOUT_APP", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
    min_containers=2,
)
def handle_sms(request: TwilioWebhook) -> str:
    """Handle an incoming SMS via Twilio and send the coach's reply."""

    # Download media from Twilio (requires auth) and convert to base64 data URLs
    media = None
    if request.media:
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        media = []
        with httpx.Client(auth=(account_sid, auth_token)) as http:
            for m in request.media:
                resp = http.get(m.url, follow_redirects=True)
                resp.raise_for_status()
                content_type = m.contentType or resp.headers.get("content-type", "image/jpeg")
                b64 = base64.b64encode(resp.content).decode()
                media.append({"url": f"data:{content_type};base64,{b64}", "contentType": content_type})

    async def _run():
        await db.init_db()
        return await run_agent(request.from_, request.body, media=media, channel="sms")

    response_text = asyncio.run(_run())

    # Send the reply SMS via Twilio, splitting on paragraph boundaries if too long
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    for chunk in _split_sms(response_text):
        client.messages.create(
            body=chunk,
            from_=request.to,
            to=request.from_,
        )

    return response_text


# Local testing
if __name__ == "__main__":
    print("Testing workout coach via Tensorlake local runner...\n")

    req = MessageRequest(
        user_id="test-user-tl",
        message="Hi! I'm new here. I'm 30 years old and want to get in shape.",
    )

    print(f"User: {req.message}")
    print("=" * 60)

    request: Request = run_local_application(handle_message, req)
    result = request.output()

    print(f"\nCoach:\n{result}")
