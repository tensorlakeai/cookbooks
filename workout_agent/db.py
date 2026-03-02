"""Database layer: asyncpg connection pool and CRUD functions."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import asyncpg

from models import (
    Goal,
    Message,
    PendingQuestion,
    UserProfile,
    WellnessLog,
    Workout,
    WorkoutLog,
)

_pool: asyncpg.Pool | None = None
_pool_loop_id: int | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the connection pool, recreating if the event loop changed."""
    global _pool, _pool_loop_id
    import asyncio

    current_loop_id = id(asyncio.get_running_loop())
    if _pool is not None and _pool_loop_id != current_loop_id:
        # Pool was created on a different event loop — discard it
        _pool = None
    if _pool is None:
        _pool = await asyncpg.create_pool(
            os.environ.get("DATABASE_URL_WORKOUT_APP", "postgresql://localhost/workout_agent"),
            min_size=2,
            max_size=10,
        )
        _pool_loop_id = current_loop_id
    return _pool


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    age INTEGER,
    gender TEXT,
    height_cm REAL,
    weight_kg REAL,
    fitness_level TEXT,
    interests JSONB DEFAULT '[]'::jsonb,
    sports JSONB DEFAULT '[]'::jsonb,
    injuries TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS goals (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    goal_type TEXT NOT NULL,
    description TEXT,
    target TEXT,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workouts (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    description TEXT,
    exercises JSONB NOT NULL,
    scheduled_date DATE,
    duration_minutes INTEGER,
    difficulty TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workout_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    workout_id INTEGER REFERENCES workouts(id),
    completed_at TIMESTAMPTZ DEFAULT now(),
    duration_minutes INTEGER,
    exercises JSONB,
    perceived_effort INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS wellness_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    log_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    logged_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pending_questions (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    question TEXT NOT NULL,
    answered BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    answered_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_memories (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id, category);
CREATE INDEX IF NOT EXISTS idx_goals_user_active ON goals(user_id, active);
CREATE INDEX IF NOT EXISTS idx_workouts_user_date ON workouts(user_id, scheduled_date DESC);
CREATE INDEX IF NOT EXISTS idx_workout_logs_user ON workout_logs(user_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_wellness_logs_user ON wellness_logs(user_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_user ON conversation_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_questions_user ON pending_questions(user_id, answered);
"""


async def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)


# ─── Users ────────────────────────────────────────────────────────────────────

async def get_user(pool: asyncpg.Pool, user_id: str) -> UserProfile | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
    if row is None:
        return None
    return UserProfile(
        user_id=row["user_id"],
        name=row["name"],
        age=row["age"],
        gender=row["gender"],
        height_cm=row["height_cm"],
        weight_kg=row["weight_kg"],
        fitness_level=row["fitness_level"],
        interests=json.loads(row["interests"]) if row["interests"] else [],
        sports=json.loads(row["sports"]) if row["sports"] else [],
        injuries=row["injuries"],
    )


async def upsert_user(pool: asyncpg.Pool, profile: UserProfile) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, name, age, gender, height_cm, weight_kg,
                               fitness_level, interests, sports, injuries, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, now())
            ON CONFLICT (user_id) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, users.name),
                age = COALESCE(EXCLUDED.age, users.age),
                gender = COALESCE(EXCLUDED.gender, users.gender),
                height_cm = COALESCE(EXCLUDED.height_cm, users.height_cm),
                weight_kg = COALESCE(EXCLUDED.weight_kg, users.weight_kg),
                fitness_level = COALESCE(EXCLUDED.fitness_level, users.fitness_level),
                interests = CASE WHEN EXCLUDED.interests != '[]'::jsonb
                            THEN EXCLUDED.interests ELSE users.interests END,
                sports = CASE WHEN EXCLUDED.sports != '[]'::jsonb
                         THEN EXCLUDED.sports ELSE users.sports END,
                injuries = COALESCE(EXCLUDED.injuries, users.injuries),
                updated_at = now()
            """,
            profile.user_id,
            profile.name,
            profile.age,
            profile.gender,
            profile.height_cm,
            profile.weight_kg,
            profile.fitness_level,
            json.dumps(profile.interests),
            json.dumps(profile.sports),
            profile.injuries,
        )


# ─── Goals ────────────────────────────────────────────────────────────────────

async def get_active_goal(pool: asyncpg.Pool, user_id: str) -> Goal | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM goals WHERE user_id = $1 AND active = true ORDER BY created_at DESC LIMIT 1",
            user_id,
        )
    if row is None:
        return None
    return Goal(
        id=row["id"],
        user_id=row["user_id"],
        goal_type=row["goal_type"],
        description=row["description"],
        target=row["target"],
        active=row["active"],
    )


async def create_goal(pool: asyncpg.Pool, goal: Goal) -> int:
    async with pool.acquire() as conn:
        # Deactivate existing goals first
        await conn.execute(
            "UPDATE goals SET active = false WHERE user_id = $1 AND active = true",
            goal.user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO goals (user_id, goal_type, description, target, active)
            VALUES ($1, $2, $3, $4, true) RETURNING id
            """,
            goal.user_id,
            goal.goal_type,
            goal.description,
            goal.target,
        )
    return row["id"]


# ─── Workouts ─────────────────────────────────────────────────────────────────

async def create_workout(pool: asyncpg.Pool, workout: Workout) -> int:
    exercises_json = json.dumps([e.model_dump() for e in workout.exercises])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workouts (user_id, title, description, exercises,
                                  scheduled_date, duration_minutes, difficulty)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) RETURNING id
            """,
            workout.user_id,
            workout.title,
            workout.description,
            exercises_json,
            workout.scheduled_date,
            workout.duration_minutes,
            workout.difficulty,
        )
    return row["id"]


async def get_recent_workouts(
    pool: asyncpg.Pool, user_id: str, limit: int = 5
) -> list[Workout]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM workouts WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id,
            limit,
        )
    results = []
    for row in rows:
        exercises = json.loads(row["exercises"]) if row["exercises"] else []
        results.append(
            Workout(
                id=row["id"],
                user_id=row["user_id"],
                title=row["title"],
                description=row["description"],
                exercises=exercises,
                scheduled_date=row["scheduled_date"],
                duration_minutes=row["duration_minutes"],
                difficulty=row["difficulty"],
            )
        )
    return results


# ─── Workout Logs ─────────────────────────────────────────────────────────────

async def create_workout_log(pool: asyncpg.Pool, log: WorkoutLog) -> int:
    exercises_json = json.dumps([e.model_dump() for e in log.exercises]) if log.exercises else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workout_logs (user_id, workout_id, duration_minutes,
                                      exercises, perceived_effort, notes)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6) RETURNING id
            """,
            log.user_id,
            log.workout_id,
            log.duration_minutes,
            exercises_json,
            log.perceived_effort,
            log.notes,
        )
    return row["id"]


async def update_workout_log(
    pool: asyncpg.Pool, log_id: int, user_id: str, log: WorkoutLog
) -> bool:
    exercises_json = json.dumps([e.model_dump() for e in log.exercises]) if log.exercises else None
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE workout_logs
            SET exercises = COALESCE($1::jsonb, exercises),
                duration_minutes = COALESCE($2, duration_minutes),
                perceived_effort = COALESCE($3, perceived_effort),
                notes = COALESCE($4, notes)
            WHERE id = $5 AND user_id = $6
            """,
            exercises_json,
            log.duration_minutes,
            log.perceived_effort,
            log.notes,
            log_id,
            user_id,
        )
    return result == "UPDATE 1"


async def get_workout_logs(
    pool: asyncpg.Pool, user_id: str, limit: int = 10
) -> list[WorkoutLog]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM workout_logs WHERE user_id = $1 ORDER BY completed_at DESC LIMIT $2",
            user_id,
            limit,
        )
    results = []
    for row in rows:
        exercises = json.loads(row["exercises"]) if row["exercises"] else []
        results.append(
            WorkoutLog(
                id=row["id"],
                user_id=row["user_id"],
                workout_id=row["workout_id"],
                completed_at=row["completed_at"],
                duration_minutes=row["duration_minutes"],
                exercises=exercises,
                perceived_effort=row["perceived_effort"],
                notes=row["notes"],
            )
        )
    return results


# ─── Wellness Logs ────────────────────────────────────────────────────────────

async def create_wellness_log(pool: asyncpg.Pool, log: WellnessLog) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO wellness_logs (user_id, log_type, payload)
            VALUES ($1, $2, $3::jsonb) RETURNING id
            """,
            log.user_id,
            log.log_type,
            json.dumps(log.payload),
        )
    return row["id"]


async def update_wellness_log(
    pool: asyncpg.Pool, log_id: int, user_id: str, payload: dict
) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE wellness_logs
            SET payload = $1::jsonb
            WHERE id = $2 AND user_id = $3
            """,
            json.dumps(payload),
            log_id,
            user_id,
        )
    return result == "UPDATE 1"


async def get_wellness_logs(
    pool: asyncpg.Pool, user_id: str, log_type: str | None = None, limit: int = 10
) -> list[WellnessLog]:
    async with pool.acquire() as conn:
        if log_type:
            rows = await conn.fetch(
                """SELECT * FROM wellness_logs
                   WHERE user_id = $1 AND log_type = $2
                   ORDER BY logged_at DESC LIMIT $3""",
                user_id,
                log_type,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM wellness_logs WHERE user_id = $1 ORDER BY logged_at DESC LIMIT $2",
                user_id,
                limit,
            )
    return [
        WellnessLog(
            id=row["id"],
            user_id=row["user_id"],
            log_type=row["log_type"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            logged_at=row["logged_at"],
        )
        for row in rows
    ]


# ─── Conversation History ─────────────────────────────────────────────────────

async def save_message(pool: asyncpg.Pool, user_id: str, role: str, content: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversation_history (user_id, role, content) VALUES ($1, $2, $3)",
            user_id,
            role,
            content,
        )


async def get_conversation_history(
    pool: asyncpg.Pool, user_id: str, limit: int = 50
) -> list[Message]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT role, content FROM conversation_history
               WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2""",
            user_id,
            limit,
        )
    # Reverse so oldest first
    return [Message(role=row["role"], content=row["content"]) for row in reversed(rows)]


# ─── Pending Questions ────────────────────────────────────────────────────────

async def add_pending_question(pool: asyncpg.Pool, user_id: str, question: str) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO pending_questions (user_id, question) VALUES ($1, $2) RETURNING id",
            user_id,
            question,
        )
    return row["id"]


async def get_pending_questions(pool: asyncpg.Pool, user_id: str) -> list[PendingQuestion]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM pending_questions WHERE user_id = $1 AND answered = false ORDER BY created_at",
            user_id,
        )
    return [
        PendingQuestion(
            id=row["id"],
            user_id=row["user_id"],
            question=row["question"],
            answered=row["answered"],
        )
        for row in rows
    ]


async def mark_question_answered(pool: asyncpg.Pool, question_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pending_questions SET answered = true, answered_at = now() WHERE id = $1",
            question_id,
        )


# ─── User Memories ────────────────────────────────────────────────────────

async def save_user_memory(
    pool: asyncpg.Pool, user_id: str, category: str, content: str
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_memories (user_id, category, content)
            VALUES ($1, $2, $3) RETURNING id
            """,
            user_id,
            category,
            content,
        )
    return row["id"]


async def get_user_memories(
    pool: asyncpg.Pool, user_id: str, category: str | None = None
) -> list[dict]:
    async with pool.acquire() as conn:
        if category:
            rows = await conn.fetch(
                """SELECT id, category, content, created_at FROM user_memories
                   WHERE user_id = $1 AND category = $2
                   ORDER BY created_at DESC""",
                user_id,
                category,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, category, content, created_at FROM user_memories
                   WHERE user_id = $1 ORDER BY created_at DESC""",
                user_id,
            )
    return [
        {
            "id": row["id"],
            "category": row["category"],
            "content": row["content"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def update_user_memory(pool: asyncpg.Pool, memory_id: int, content: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_memories SET content = $1, updated_at = now() WHERE id = $2",
            content,
            memory_id,
        )


async def delete_user_memory(pool: asyncpg.Pool, memory_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM user_memories WHERE id = $1", memory_id)


# ─── Reminder Helpers ─────────────────────────────────────────────────────────

async def get_inactive_users(pool: asyncpg.Pool, days: int = 3) -> list[str]:
    """Find users whose last conversation message is older than `days` days."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT u.user_id
            FROM users u
            LEFT JOIN conversation_history ch ON u.user_id = ch.user_id
            GROUP BY u.user_id
            HAVING MAX(ch.created_at) < now() - ($1 || ' days')::interval
               OR MAX(ch.created_at) IS NULL
            """,
            str(days),
        )
    return [row["user_id"] for row in rows]
