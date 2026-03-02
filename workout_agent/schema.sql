-- Workout Coach Agent — PostgreSQL Schema

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    age INTEGER,
    gender TEXT,
    height_cm REAL,
    weight_kg REAL,
    fitness_level TEXT,  -- beginner, intermediate, advanced
    interests JSONB DEFAULT '[]'::jsonb,
    sports JSONB DEFAULT '[]'::jsonb,
    injuries TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS goals (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    goal_type TEXT NOT NULL,       -- e.g. 'weight_loss', 'muscle_gain', 'endurance', 'flexibility'
    description TEXT,
    target TEXT,                   -- e.g. '10k in under 50 min', 'lose 5kg'
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workouts (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    description TEXT,
    exercises JSONB NOT NULL,      -- [{name, sets, reps, weight, rest, notes}, ...]
    scheduled_date DATE,
    duration_minutes INTEGER,
    difficulty TEXT,               -- easy, moderate, hard
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workout_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    workout_id INTEGER REFERENCES workouts(id),
    completed_at TIMESTAMPTZ DEFAULT now(),
    duration_minutes INTEGER,
    exercises JSONB,               -- [{name, sets, reps, weight, notes}, ...]
    perceived_effort INTEGER,      -- 1-10 RPE scale
    notes TEXT
);

CREATE TABLE IF NOT EXISTS wellness_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    log_type TEXT NOT NULL,        -- 'food', 'mood', 'sleep', 'weight', 'general'
    payload JSONB NOT NULL,        -- flexible data per log_type
    logged_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    role TEXT NOT NULL,            -- 'user' or 'assistant'
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
    category TEXT NOT NULL,        -- 'preference', 'observation', 'context', 'medical', 'lifestyle'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id, category);
CREATE INDEX IF NOT EXISTS idx_goals_user_active ON goals(user_id, active);
CREATE INDEX IF NOT EXISTS idx_workouts_user_date ON workouts(user_id, scheduled_date DESC);
CREATE INDEX IF NOT EXISTS idx_workout_logs_user ON workout_logs(user_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_wellness_logs_user ON wellness_logs(user_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_user ON conversation_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_questions_user ON pending_questions(user_id, answered);
