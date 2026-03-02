"""Agent wiring — creates the workout coach agent and provides run_agent()."""

from __future__ import annotations

from agents import Agent, Runner

import db
from models import UserContext
from prompts import build_dynamic_prompt
from tools import ALL_TOOLS

MODEL = "gpt-5.2"


def _build_agent(system_prompt: str) -> Agent[UserContext]:
    return Agent[UserContext](
        name="Coach",
        model=MODEL,
        instructions=system_prompt,
        tools=ALL_TOOLS,
    )


async def run_agent(
    user_id: str,
    message: str,
    media: list[dict] | None = None,
    channel: str = "api",
) -> str:
    """Handle a single user message: load context, run agent, persist history.

    Args:
        user_id: The user identifier.
        message: The text message from the user.
        media: Optional list of media dicts with 'url' and 'contentType' keys (MMS images).
        channel: The channel the message came from ("api" or "sms").
    """
    pool = await db.get_pool()

    # Ensure user row exists
    profile = await db.get_user(pool, user_id)
    if profile is None:
        from models import UserProfile
        profile = UserProfile(user_id=user_id)
        await db.upsert_user(pool, profile)

    # Load context
    active_goal = await db.get_active_goal(pool, user_id)
    pending_qs = await db.get_pending_questions(pool, user_id)

    # Load memories
    memories = await db.get_user_memories(pool, user_id)

    # Build dynamic system prompt
    system_prompt = build_dynamic_prompt(profile, active_goal, pending_qs, memories, channel=channel)

    # Load conversation history
    history = await db.get_conversation_history(pool, user_id)
    messages = [{"role": m.role, "content": m.content} for m in history]

    # Build user message content — multimodal if media is attached
    if media:
        content = [{"type": "input_text", "text": message}]
        for item in media:
            content.append({
                "type": "input_image",
                "image_url": item["url"],
            })
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": message})

    # Build agent and run
    agent = _build_agent(system_prompt)
    ctx = UserContext(
        user_id=user_id,
        db_pool=pool,
        profile=profile,
        active_goal=active_goal,
        pending_questions=pending_qs,
    )

    result = await Runner.run(agent, messages, context=ctx)

    # Extract response text
    response_text = result.final_output

    # Persist both messages
    await db.save_message(pool, user_id, "user", message)
    await db.save_message(pool, user_id, "assistant", response_text)

    return response_text


async def generate_reminder(user_id: str) -> str:
    """Generate a personalized motivational reminder for an inactive user."""
    pool = await db.get_pool()
    profile = await db.get_user(pool, user_id)
    active_goal = await db.get_active_goal(pool, user_id)

    reminder_prompt = (
        "The user hasn't checked in for a few days. "
        "Send a short, warm motivational message encouraging them to get back on track. "
        "Reference their goal or recent progress if available. Keep it under 3 sentences."
    )

    memories = await db.get_user_memories(pool, user_id)
    system_prompt = build_dynamic_prompt(profile, active_goal, [], memories)
    agent = _build_agent(system_prompt)
    ctx = UserContext(user_id=user_id, db_pool=pool, profile=profile, active_goal=active_goal)

    result = await Runner.run(
        agent,
        [{"role": "user", "content": reminder_prompt}],
        context=ctx,
    )

    response_text = result.final_output
    await db.save_message(pool, user_id, "assistant", f"[Reminder] {response_text}")
    return response_text
