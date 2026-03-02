"""Local testing script — interactive chat with the workout coach.

Usage:
    1. Start PostgreSQL and create the database:
       createdb workout_agent

    2. Set environment variables (or use a .env file):
       export DATABASE_URL=postgresql://localhost/workout_agent
       export OPENAI_API_KEY=sk-...

    3. Run:
       python test_local.py
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from agent import run_agent
import db


async def interactive():
    """Interactive chat loop with the workout coach."""
    await db.init_db()

    user_id = "test-user-local"
    print("=" * 60)
    print("  Workout Coach Agent — Interactive Mode")
    print("  Type your message and press Enter.")
    print("  Type 'quit' or 'exit' to stop.")
    print("=" * 60)

    while True:
        try:
            msg = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not msg:
            continue
        if msg.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        response = await run_agent(user_id, msg)
        print(f"\nCoach: {response}")


async def scripted():
    """Run a scripted 8-message conversation for automated testing."""
    await db.init_db()

    user_id = "test-user-scripted"
    print("=" * 60)
    print("  Workout Coach Agent — Scripted Test")
    print("=" * 60)

    conversations = [
        "Hi! I'm Alex, 28 years old, male, 180cm tall and 82kg. I'd say I'm at an intermediate fitness level.",
        "I'm interested in weightlifting and running. I have a minor lower back issue from an old injury.",
        "I want to build muscle while improving my 5K time. My target is to run 5K in under 22 minutes and gain 3kg of lean mass.",
        "Can you create a workout plan for me for today? Something that combines strength and cardio.",
        "I just finished the workout! I did all the exercises. It took me about 50 minutes and I'd rate the effort a 7 out of 10.",
        "For lunch I had grilled chicken breast with rice and vegetables, probably around 600 calories.",
        "I'm feeling great today, energy level about 8 out of 10. Really motivated after that workout!",
        "What should I do for tomorrow's workout?",
    ]

    for i, msg in enumerate(conversations, 1):
        print(f"\n{'─' * 60}")
        print(f"[Message {i}] You: {msg}")
        print(f"{'─' * 60}")

        response = await run_agent(user_id, msg)
        print(f"\nCoach: {response}")

    print(f"\n{'=' * 60}")
    print("Scripted test complete!")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "interactive"

    if mode == "scripted":
        asyncio.run(scripted())
    else:
        asyncio.run(interactive())
