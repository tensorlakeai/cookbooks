"""
Sequential Agent Example on TensorLake
=======================================
Demonstrates sequential orchestration where each LLM agent runs as its
own TensorLake function (separate container). The orchestrator calls them
in strict order, passing each stage's output as input to the next.

Pipeline (each stage = separate container):
  1. Code Writer   -> generates initial code
  2. Code Reviewer -> reviews the generated code
  3. Code Refactorer -> improves code based on review feedback

Based on: https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/
"""

import asyncio
from tensorlake.applications import application, function, run_local_application, Request, Image


AGENT_IMAGE = Image().run("pip install google-adk")


# --- Stage 1: Code Writer (own container) ---

@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def write_code(specification: str) -> str:
    """LLM agent that generates Python code from a specification."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="CodeWriterAgent",
            instruction=(
                "You are a Python Code Generator. Based on the user's specification, "
                "write clean, well-structured Python code. Output ONLY the code, "
                "no explanations."
            ),
            description="Generates initial Python code from a specification.",
        )

        runner = InMemoryRunner(agent=agent, app_name="writer")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="writer"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=specification)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


# --- Stage 2: Code Reviewer (own container) ---

@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def review_code(generated_code: str) -> str:
    """LLM agent that reviews generated code and provides feedback."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="CodeReviewerAgent",
            instruction=(
                "You are a Code Reviewer. Review the following Python code and "
                "provide specific, actionable feedback on:\n"
                "1. Code correctness\n"
                "2. Edge cases\n"
                "3. Code style and best practices\n\n"
                "Output ONLY the review comments."
            ),
            description="Reviews generated code and provides feedback.",
        )

        runner = InMemoryRunner(agent=agent, app_name="reviewer")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="reviewer"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=f"Review this code:\n\n{generated_code}")]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


# --- Stage 3: Code Refactorer (own container) ---

@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def refactor_code(generated_code: str, review_comments: str) -> str:
    """LLM agent that refactors code based on review feedback."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="CodeRefactorerAgent",
            instruction=(
                "You are a Code Refactoring Specialist. Improve the code "
                "based on the review feedback provided. "
                "Output the improved, refactored Python code. Output ONLY the code."
            ),
            description="Refactors code based on review comments.",
        )

        prompt = (
            f"Original code:\n{generated_code}\n\n"
            f"Review feedback:\n{review_comments}\n\n"
            f"Refactor the code to address the feedback."
        )

        runner = InMemoryRunner(agent=agent, app_name="refactorer")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="refactorer"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=prompt)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


# --- Orchestrator: calls stages sequentially, passing outputs forward ---

@application()
@function(image=AGENT_IMAGE)
def code_pipeline(specification: str) -> str:
    """Sequential pipeline: write -> review -> refactor.

    Each stage runs in its own TensorLake container. The orchestrator
    chains them together, passing each stage's output as input to the next.
    """
    # Stage 1: Generate code from specification
    generated_code = write_code(specification=specification)

    # Stage 2: Review the generated code
    review_comments = review_code(generated_code=generated_code)

    # Stage 3: Refactor based on review feedback
    refactored_code = refactor_code(
        generated_code=generated_code,
        review_comments=review_comments,
    )

    return refactored_code


if __name__ == "__main__":
    spec = (
        "Write a Python function that takes a list of integers and returns "
        "the top K most frequent elements. Handle edge cases."
    )
    request: Request = run_local_application(code_pipeline, specification=spec)
    print("=== Final Refactored Code ===")
    print(request.output())
