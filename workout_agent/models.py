"""Pydantic data models and UserContext for the workout coach agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


# --- User Profile ---

class UserProfile(BaseModel):
    user_id: str
    name: str | None = None
    age: int | None = None
    gender: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    fitness_level: str | None = None
    interests: list[str] = []
    sports: list[str] = []
    injuries: str | None = None


# --- Goals ---

class Goal(BaseModel):
    id: int | None = None
    user_id: str
    goal_type: str
    description: str | None = None
    target: str | None = None
    active: bool = True


# --- Workouts ---

class Exercise(BaseModel):
    name: str
    sets: int | None = None
    reps: str | None = None  # "12" or "8-12" or "30s"
    weight: str | None = None
    rest: str | None = None
    notes: str | None = None


class Workout(BaseModel):
    id: int | None = None
    user_id: str
    title: str
    description: str | None = None
    exercises: list[Exercise] = []
    scheduled_date: date | None = None
    duration_minutes: int | None = None
    difficulty: str | None = None


# --- Workout Logs ---

class ExerciseLog(BaseModel):
    name: str
    sets: int | None = None
    reps: str | None = None
    weight: str | None = None
    notes: str | None = None


class WorkoutLog(BaseModel):
    id: int | None = None
    user_id: str
    workout_id: int | None = None
    completed_at: datetime | None = None
    duration_minutes: int | None = None
    exercises: list[ExerciseLog] = []
    perceived_effort: int | None = None  # 1-10 RPE
    notes: str | None = None


# --- Wellness Logs ---

class WellnessLog(BaseModel):
    id: int | None = None
    user_id: str
    log_type: str  # food, mood, sleep, weight, general
    payload: dict[str, Any] = {}
    logged_at: datetime | None = None


# --- Conversation ---

class Message(BaseModel):
    role: str  # user or assistant
    content: str


# --- Pending Questions ---

class PendingQuestion(BaseModel):
    id: int | None = None
    user_id: str
    question: str
    answered: bool = False


# --- Agent RunContext ---

@dataclass
class UserContext:
    """Passed as RunContext to every tool invocation."""
    user_id: str
    db_pool: Any  # asyncpg.Pool
    profile: UserProfile | None = None
    active_goal: Goal | None = None
    pending_questions: list[PendingQuestion] = field(default_factory=list)
