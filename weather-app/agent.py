import anthropic
from prompts import SYSTEM_PROMPT

# Built-in Anthropic tools - no custom implementation needed!
# These are server-side tools that Anthropic handles automatically.
TOOLS = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    },
    {
        "type": "web_fetch_20250910",
        "name": "web_fetch",
        "max_uses": 3,
    }
]


def run_agent(user_message: str) -> str:
    """Run the weather agent with a user message.

    Uses Claude with built-in web_search and web_fetch tools.
    Anthropic's API handles the tool execution server-side.
    """
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_message}]

    # Use beta.messages for server-side tool features
    response = client.beta.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
        betas=["web-fetch-2025-09-10"]
    )

    return extract_text(response.content)


def extract_text(content: list) -> str:
    """Extract text from response content blocks."""
    text_parts = []
    for block in content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "".join(text_parts)
