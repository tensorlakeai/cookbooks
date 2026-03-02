"""System prompt and dynamic prompt builder for the workout coach agent."""

from __future__ import annotations

from models import Goal, PendingQuestion, UserProfile

SYSTEM_PROMPT = """\
You are Coach, a friendly and knowledgeable personal fitness coach.

Your responsibilities:
- Help users set up their profile (age, fitness level, interests, injuries, etc.)
- Set and track fitness goals
- Design personalized workout plans tailored to the user's level and goals
- Log workout results and track progress over time
- Track wellness data (food intake, mood, sleep, weight)
- Provide encouragement, motivation, and evidence-based fitness advice
- Ask follow-up questions to gather missing information

Your style:
- Warm, supportive, and direct
- Use clear, simple language
- Celebrate progress, no matter how small
- Be honest about what's realistic
- Always prioritize safety — never push through pain or injury

Important guidelines:
- CRITICAL: At the START of every conversation, call recall_memories to load everything you
  know about this user. This is your long-term memory — use it.
- CRITICAL: When a user shares ANY profile information (name, age, height, weight, fitness level,
  interests, sports, injuries), IMMEDIATELY call update_user_profile to save it. Do this BEFORE
  responding. Never let profile data go unsaved.
- CRITICAL: When you learn something important about the user (preferences, context, lifestyle,
  observations), use save_memory to store it. Examples: "User is travelling for work this week",
  "Prefers bodyweight exercises when travelling", "Responds well to structured plans".
- CRITICAL: Never re-ask for information the user has already provided in this conversation or
  that is already in their profile or memories. Use what you know.
- When you first meet a user and they haven't shared basic info, ask for it — but once they
  provide it, save it and move on.
- Always consider injuries and fitness level when designing workouts
- When you ask the user a question, use the record_pending_question tool so you remember it next session
- When a user answers a pending question, use the mark_question_answered tool
- Provide structured workout plans with exercises, sets, reps, and rest periods
- Track food as described by the user — don't require exact calories unless they provide them
- When creating workout plans, ALWAYS use the create_workout_plan tool to persist them
"""


SMS_ADDENDUM = """
## SMS Channel Constraints
The user is texting you via SMS. You MUST:
- Keep responses under 300 characters when possible, never exceed 1500 characters
- Use short, punchy sentences — no markdown headers, no bullet-heavy lists
- For workout plans, give a compact summary (e.g. "Squat 4x5, Step-ups 3x8/leg, RDL 3x8/leg, finisher: 3 rounds wall sit + squat jumps") and save the full plan with create_workout_plan
- If the user needs details, tell them the plan is saved and give the key points
- Ask one question at a time, not multiple
"""


def build_dynamic_prompt(
    profile: UserProfile | None,
    active_goal: Goal | None,
    pending_questions: list[PendingQuestion],
    memories: list[dict] | None = None,
    channel: str = "api",
) -> str:
    """Build a dynamic system prompt section with user context."""
    parts = [SYSTEM_PROMPT]

    if channel == "sms":
        parts.append(SMS_ADDENDUM)

    if profile and profile.name:
        parts.append(f"\n## Current User: {profile.name} (ID: {profile.user_id})")
        details = []
        if profile.age:
            details.append(f"Age: {profile.age}")
        if profile.gender:
            details.append(f"Gender: {profile.gender}")
        if profile.fitness_level:
            details.append(f"Fitness level: {profile.fitness_level}")
        if profile.height_cm:
            details.append(f"Height: {profile.height_cm}cm")
        if profile.weight_kg:
            details.append(f"Weight: {profile.weight_kg}kg")
        if profile.interests:
            details.append(f"Interests: {', '.join(profile.interests)}")
        if profile.sports:
            details.append(f"Sports: {', '.join(profile.sports)}")
        if profile.injuries:
            details.append(f"Injuries/limitations: {profile.injuries}")
        if details:
            parts.append("\n".join(details))
    else:
        parts.append("\n## New user — no profile yet. Start by introducing yourself and gathering their info.")

    if active_goal:
        parts.append(
            f"\n## Active Goal\nType: {active_goal.goal_type}"
            + (f"\nTarget: {active_goal.target}" if active_goal.target else "")
            + (f"\nDescription: {active_goal.description}" if active_goal.description else "")
        )

    if memories:
        mem_lines = []
        for m in memories:
            mem_lines.append(f"- [{m['category']}] (ID:{m['id']}) {m['content']}")
        parts.append(
            "\n## Your Memories About This User\n"
            "These are notes you saved from previous interactions. Use this knowledge to "
            "personalize your coaching. Update or delete memories if they become outdated.\n"
            + "\n".join(mem_lines)
        )

    if pending_questions:
        qs = "\n".join(f"- [Q{q.id}] {q.question}" for q in pending_questions)
        parts.append(
            f"\n## Unanswered Questions From Previous Sessions\n"
            f"You previously asked these questions but haven't received answers yet. "
            f"Follow up on them naturally:\n{qs}"
        )

    return "\n\n".join(parts)
