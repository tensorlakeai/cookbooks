# GSPO Agentic RL with TensorLake Sandboxes

Reinforcement learning for code generation using **GSPO** (Group Sequence Policy Optimization) with [TensorLake sandboxes](https://docs.tensorlake.ai/sandboxes/gspo-agentic-rl) as a safe reward oracle.

Model-generated Python is never executed locally — each completion runs inside an isolated TensorLake sandbox that scores it against a hidden pytest suite.

## How it works

1. **Phase 1 — SFT warmup**: Supervised fine-tuning on correct solutions so the model produces valid Python before RL begins.
2. **Phase 2 — GSPO**: `GRPOTrainer` with `importance_sampling_level="sequence"` dispatches G completions per step to parallel sandboxes, collects pass rates as rewards, and updates the policy.

GSPO clips the importance sampling ratio at the sequence level (vs. per-token in GRPO), which is better suited to long function bodies.

## Scripts

| File | Description |
|---|---|
| `gspo_trainer.py` | Full two-phase RL training pipeline |

## Setup

### Prerequisites

- Python 3.10+
- A [TensorLake](https://tensorlake.ai) account and API key

### Install Python dependencies

```bash
pip install python-dotenv torch datasets transformers trl openenv tensorlake rich
```

Install the following libraries instead for CPU only systems:

```
pip install python-dotenv datasets transformers trl openenv tensorlake rich
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### Configure your API key

If you do not have one, you may obtain one from [https://tensorlake.ai](https://tensorlake.ai).

```bash
cp .env.example .env
# Edit .env and set your TENSORLAKE_API_KEY
```

## Running the Python trainer

**Quick smoke test** (~5 minutes, CPU-only):

```bash
python gspo_trainer.py --smoke
```

Uses 3 tasks, 20 SFT steps, 1 GSPO epoch.

**Full training run** (~30 minutes, CPU-only):

```bash
python gspo_trainer.py
```

Uses 10 tasks, 60 SFT steps, 3 GSPO epochs.

The script prints live output including:
- Baseline evaluation before training
- SFT warmup loss per step
- Best completions as reward > 0 is observed during GSPO
- Final per-task pass rates on held-out functions

Output model checkpoints are saved to `./gspo_coder/`.

## Model and tasks

- **Model**: `HuggingFaceTB/SmolLM2-135M-Instruct` — downloaded automatically from HuggingFace, no GPU required.
- **Tasks**: 10 Python functions (`sum_list`, `is_palindrome`, `fizzbuzz`, `count_vowels`, `flatten`, `max_consecutive`, `second_largest`, `run_length_encode`, `rotate_list`, `word_frequency`).
- **Train/eval split**: 75% train, 25% held out for evaluation.

## Expected results

After the full training run, expect ~25% average test pass rate on held-out functions. For a 135M parameter model trained for ~30 minutes on CPU this is a meaningful improvement over the ~0% zero-shot baseline.

To push higher without extra hardware:
- Add more SFT examples before GSPO
- Switch to `Qwen2.5-0.5B-Instruct` (4× more parameters, similar training time)
