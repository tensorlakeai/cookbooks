"""Agent tools — @function_tool definitions wrapping DB operations."""

from __future__ import annotations

import json
from datetime import date

from agents import RunContextWrapper, function_tool

import db
from models import (
    Exercise,
    ExerciseLog,
    Goal,
    PendingQuestion,
    UserContext,
    UserProfile,
    WellnessLog,
    Workout,
    WorkoutLog,
)


# ─── Profile Tools ────────────────────────────────────────────────────────────

@function_tool
async def get_user_profile(ctx: RunContextWrapper[UserContext]) -> str:
    """Retrieve the current user's profile information."""
    profile = await db.get_user(ctx.context.db_pool, ctx.context.user_id)
    if profile is None:
        return "No profile found for this user yet."
    return profile.model_dump_json()


@function_tool
async def update_user_profile(
    ctx: RunContextWrapper[UserContext],
    name: str | None = None,
    age: int | None = None,
    gender: str | None = None,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    fitness_level: str | None = None,
    interests: list[str] | None = None,
    sports: list[str] | None = None,
    injuries: str | None = None,
) -> str:
    """Update the user's profile. Only provided fields will be updated."""
    profile = UserProfile(
        user_id=ctx.context.user_id,
        name=name,
        age=age,
        gender=gender,
        height_cm=height_cm,
        weight_kg=weight_kg,
        fitness_level=fitness_level,
        interests=interests or [],
        sports=sports or [],
        injuries=injuries,
    )
    await db.upsert_user(ctx.context.db_pool, profile)
    return "Profile updated successfully."


# ─── Goal Tools ───────────────────────────────────────────────────────────────

@function_tool
async def get_current_goal(ctx: RunContextWrapper[UserContext]) -> str:
    """Get the user's current active fitness goal."""
    goal = await db.get_active_goal(ctx.context.db_pool, ctx.context.user_id)
    if goal is None:
        return "No active goal set."
    return goal.model_dump_json()


@function_tool
async def set_new_goal(
    ctx: RunContextWrapper[UserContext],
    goal_type: str,
    description: str | None = None,
    target: str | None = None,
) -> str:
    """Set a new fitness goal. Deactivates any previous goal.

    Args:
        goal_type: Type of goal (e.g. 'weight_loss', 'muscle_gain', 'endurance', 'flexibility', 'general_fitness')
        description: Detailed description of the goal
        target: Specific measurable target (e.g. 'run 5K in under 25 min', 'lose 5kg')
    """
    goal = Goal(
        user_id=ctx.context.user_id,
        goal_type=goal_type,
        description=description,
        target=target,
    )
    goal_id = await db.create_goal(ctx.context.db_pool, goal)
    return f"New goal set (ID: {goal_id}): {goal_type}" + (f" — {target}" if target else "")


# ─── Workout Tools ────────────────────────────────────────────────────────────

@function_tool
async def create_workout_plan(
    ctx: RunContextWrapper[UserContext],
    title: str,
    exercises: list[Exercise],
    description: str | None = None,
    scheduled_date: str | None = None,
    duration_minutes: int | None = None,
    difficulty: str | None = None,
) -> str:
    """Create a workout plan for the user.

    Args:
        title: Name of the workout (e.g. 'Upper Body Strength Day')
        exercises: List of exercises, each with name, sets, reps, weight, rest, notes
        description: Overview of the workout
        scheduled_date: When to do it (YYYY-MM-DD format)
        duration_minutes: Estimated duration in minutes
        difficulty: easy, moderate, or hard
    """
    sched = date.fromisoformat(scheduled_date) if scheduled_date else None
    workout = Workout(
        user_id=ctx.context.user_id,
        title=title,
        description=description,
        exercises=exercises,
        scheduled_date=sched,
        duration_minutes=duration_minutes,
        difficulty=difficulty,
    )
    workout_id = await db.create_workout(ctx.context.db_pool, workout)
    return f"Workout plan created (ID: {workout_id}): {title} — {len(exercises)} exercises"


@function_tool
async def get_recent_workouts(
    ctx: RunContextWrapper[UserContext],
    limit: int = 5,
) -> str:
    """Get the user's recent workout plans.

    Args:
        limit: Number of recent workouts to retrieve (default 5)
    """
    workouts = await db.get_recent_workouts(ctx.context.db_pool, ctx.context.user_id, limit)
    if not workouts:
        return "No workout plans found."
    return json.dumps([w.model_dump(mode="json") for w in workouts], indent=2)


# ─── Workout Log Tools ────────────────────────────────────────────────────────

@function_tool
async def log_workout_result(
    ctx: RunContextWrapper[UserContext],
    exercises: list[ExerciseLog],
    workout_id: int | None = None,
    duration_minutes: int | None = None,
    perceived_effort: int | None = None,
    notes: str | None = None,
) -> str:
    """Log a completed workout result reported by the user.

    Args:
        exercises: List of exercises completed, each with name, sets, reps, weight, notes
        workout_id: ID of the prescribed workout plan (if following one)
        duration_minutes: How long the workout took
        perceived_effort: Rate of perceived exertion 1-10
        notes: Any additional notes
    """
    log = WorkoutLog(
        user_id=ctx.context.user_id,
        workout_id=workout_id,
        duration_minutes=duration_minutes,
        exercises=exercises,
        perceived_effort=perceived_effort,
        notes=notes,
    )
    log_id = await db.create_workout_log(ctx.context.db_pool, log)
    return f"Workout logged (ID: {log_id}). Great work!"


@function_tool
async def update_workout_log(
    ctx: RunContextWrapper[UserContext],
    log_id: int,
    exercises: list[ExerciseLog] | None = None,
    duration_minutes: int | None = None,
    perceived_effort: int | None = None,
    notes: str | None = None,
) -> str:
    """Update an existing workout log entry, e.g. when the user corrects sets, reps, or weight.

    Args:
        log_id: ID of the workout log to update
        exercises: Updated list of exercises (replaces the entire exercises list if provided)
        duration_minutes: Updated duration
        perceived_effort: Updated RPE 1-10
        notes: Updated notes
    """
    log = WorkoutLog(
        user_id=ctx.context.user_id,
        duration_minutes=duration_minutes,
        exercises=exercises or [],
        perceived_effort=perceived_effort,
        notes=notes,
    )
    updated = await db.update_workout_log(
        ctx.context.db_pool, log_id, ctx.context.user_id, log
    )
    if updated:
        return f"Workout log {log_id} updated successfully."
    return f"Could not update workout log {log_id}. It may not exist or belong to this user."


@function_tool
async def get_workout_history(
    ctx: RunContextWrapper[UserContext],
    limit: int = 10,
) -> str:
    """Get the user's recent workout log history.

    Args:
        limit: Number of recent logs to retrieve (default 10)
    """
    logs = await db.get_workout_logs(ctx.context.db_pool, ctx.context.user_id, limit)
    if not logs:
        return "No workout logs found."
    return json.dumps([l.model_dump(mode="json") for l in logs], indent=2)


# ─── Wellness Tools ───────────────────────────────────────────────────────────

@function_tool
async def log_food(
    ctx: RunContextWrapper[UserContext],
    meal: str,
    description: str,
    calories: int | None = None,
    protein_g: float | None = None,
) -> str:
    """Log a food/meal entry.

    Args:
        meal: Meal type (breakfast, lunch, dinner, snack)
        description: What the user ate
        calories: Estimated calories (optional)
        protein_g: Estimated protein in grams (optional)
    """
    payload = {"meal": meal, "description": description}
    if calories is not None:
        payload["calories"] = calories
    if protein_g is not None:
        payload["protein_g"] = protein_g
    log = WellnessLog(user_id=ctx.context.user_id, log_type="food", payload=payload)
    await db.create_wellness_log(ctx.context.db_pool, log)
    return f"Food logged: {meal} — {description}"


@function_tool
async def log_mood(
    ctx: RunContextWrapper[UserContext],
    mood: str,
    energy_level: int | None = None,
    notes: str | None = None,
) -> str:
    """Log the user's current mood and energy.

    Args:
        mood: How they're feeling (e.g. 'great', 'good', 'okay', 'tired', 'stressed')
        energy_level: Energy level 1-10 (optional)
        notes: Any context about their mood
    """
    payload: dict = {"mood": mood}
    if energy_level is not None:
        payload["energy_level"] = energy_level
    if notes:
        payload["notes"] = notes
    log = WellnessLog(user_id=ctx.context.user_id, log_type="mood", payload=payload)
    await db.create_wellness_log(ctx.context.db_pool, log)
    return f"Mood logged: {mood}" + (f" (energy: {energy_level}/10)" if energy_level else "")


@function_tool
async def log_wellness(
    ctx: RunContextWrapper[UserContext],
    log_type: str,
    payload_json: str,
) -> str:
    """Log general wellness data (sleep, weight, or custom).

    Args:
        log_type: Type of log ('sleep', 'weight', or custom)
        payload_json: JSON string with data. For sleep: {"hours": 8, "quality": "good"}. For weight: {"weight_kg": 80}. Custom: any keys.
    """
    payload = json.loads(payload_json)
    log = WellnessLog(user_id=ctx.context.user_id, log_type=log_type, payload=payload)
    await db.create_wellness_log(ctx.context.db_pool, log)
    return f"Wellness data logged: {log_type}"


@function_tool
async def update_wellness_log(
    ctx: RunContextWrapper[UserContext],
    log_id: int,
    payload_json: str,
) -> str:
    """Update an existing wellness log entry (food, mood, sleep, weight, etc.) when the user corrects previously logged data.

    Args:
        log_id: ID of the wellness log to update
        payload_json: JSON string with the corrected data. Replaces the entire payload.
    """
    payload = json.loads(payload_json)
    updated = await db.update_wellness_log(
        ctx.context.db_pool, log_id, ctx.context.user_id, payload
    )
    if updated:
        return f"Wellness log {log_id} updated successfully."
    return f"Could not update wellness log {log_id}. It may not exist or belong to this user."


@function_tool
async def get_wellness_history(
    ctx: RunContextWrapper[UserContext],
    log_type: str | None = None,
    limit: int = 10,
) -> str:
    """Get the user's recent wellness log history.

    Args:
        log_type: Filter by type ('food', 'mood', 'sleep', 'weight') or None for all
        limit: Number of recent logs to retrieve (default 10)
    """
    logs = await db.get_wellness_logs(ctx.context.db_pool, ctx.context.user_id, log_type, limit)
    if not logs:
        return "No wellness logs found."
    return json.dumps([l.model_dump(mode="json") for l in logs], indent=2, default=str)


# ─── Async Session Tools ──────────────────────────────────────────────────────

@function_tool
async def record_pending_question(
    ctx: RunContextWrapper[UserContext],
    question: str,
) -> str:
    """Record a question you asked the user so you can follow up next session.

    Args:
        question: The question you asked that hasn't been answered yet
    """
    qid = await db.add_pending_question(ctx.context.db_pool, ctx.context.user_id, question)
    return f"Question recorded (ID: {qid}). Will follow up next session."


@function_tool
async def get_unanswered_questions(ctx: RunContextWrapper[UserContext]) -> str:
    """Get all unanswered questions for the current user."""
    questions = await db.get_pending_questions(ctx.context.db_pool, ctx.context.user_id)
    if not questions:
        return "No pending questions."
    return json.dumps([q.model_dump() for q in questions], indent=2)


@function_tool
async def mark_question_answered(
    ctx: RunContextWrapper[UserContext],
    question_id: int,
) -> str:
    """Mark a pending question as answered.

    Args:
        question_id: ID of the question to mark as answered
    """
    await db.mark_question_answered(ctx.context.db_pool, question_id)
    return f"Question {question_id} marked as answered."


# ─── Memory Tools ─────────────────────────────────────────────────────────

@function_tool
async def save_memory(
    ctx: RunContextWrapper[UserContext],
    category: str,
    content: str,
) -> str:
    """Save a note/memory about the user for future sessions.

    Use this to remember important facts, preferences, observations, or context
    about the user that will help you coach them better in future conversations.

    Args:
        category: Category of memory — one of: 'preference' (likes/dislikes, equipment access),
                  'observation' (things you noticed about their form, progress, habits),
                  'context' (life situation, schedule, travel, work),
                  'medical' (injuries, conditions, limitations),
                  'lifestyle' (diet preferences, sleep habits, stress factors)
        content: The information to remember. Be specific and concise.
    """
    mid = await db.save_user_memory(ctx.context.db_pool, ctx.context.user_id, category, content)
    return f"Memory saved (ID: {mid}): [{category}] {content}"


@function_tool
async def recall_memories(
    ctx: RunContextWrapper[UserContext],
    category: str | None = None,
) -> str:
    """Recall all saved memories/notes about the user.

    Call this at the start of a conversation to refresh your knowledge about the user,
    or when you need specific context to personalize your coaching.

    Args:
        category: Filter by category ('preference', 'observation', 'context', 'medical', 'lifestyle'), or leave empty for all
    """
    memories = await db.get_user_memories(ctx.context.db_pool, ctx.context.user_id, category)
    if not memories:
        return "No memories saved for this user yet."
    return json.dumps(memories, indent=2)


@function_tool
async def update_memory(
    ctx: RunContextWrapper[UserContext],
    memory_id: int,
    content: str,
) -> str:
    """Update an existing memory with new information.

    Args:
        memory_id: ID of the memory to update
        content: The updated content
    """
    await db.update_user_memory(ctx.context.db_pool, memory_id, content)
    return f"Memory {memory_id} updated."


@function_tool
async def delete_memory(
    ctx: RunContextWrapper[UserContext],
    memory_id: int,
) -> str:
    """Delete a memory that is no longer relevant.

    Args:
        memory_id: ID of the memory to delete
    """
    await db.delete_user_memory(ctx.context.db_pool, memory_id)
    return f"Memory {memory_id} deleted."


# All tools list for the agent
ALL_TOOLS = [
    get_user_profile,
    update_user_profile,
    get_current_goal,
    set_new_goal,
    create_workout_plan,
    get_recent_workouts,
    log_workout_result,
    update_workout_log,
    get_workout_history,
    log_food,
    log_mood,
    log_wellness,
    update_wellness_log,
    get_wellness_history,
    record_pending_question,
    get_unanswered_questions,
    mark_question_answered,
    save_memory,
    recall_memories,
    update_memory,
    delete_memory,
]
