"""
Tensorlake application.
Defines the workflow for handling weather queries.
"""

from tensorlake.applications import application, function, run_local_application, Request, Image

from agent import run_agent

# Define image with required dependencies
weather_agent_image = (
    Image()
    .run("pip install anthropic")
)


@application()
@function(image=weather_agent_image, secrets=["ANTHROPIC_API_KEY"], min_containers=2)
def handle_weather_query(query: str) -> str:
    """
    Entry point for the Tensorlake workflow.

    Args:
        query: Natural language weather question

    Returns:
        The agent's response
    """
    return run_agent(query)


# For local testing with Tensorlake's local runner
if __name__ == "__main__":
    print("Testing with Tensorlake local runner...\n")

    test_query = "Can I wear white sneakers tonight in NYC?"
    print(f"Query: {test_query}")
    print("=" * 60 + "\n")

    request: Request = run_local_application(handle_weather_query, test_query)
    result = request.output()

    print(f"Response:\n{result}")
