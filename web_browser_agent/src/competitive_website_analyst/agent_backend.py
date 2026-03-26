from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from competitive_website_analyst.models import BrowserArtifact, BrowserMetadata, Company, ScoreBreakdown, Scorecard
from competitive_website_analyst.prompts import ANALYSIS_PROMPT, BROWSER_PROMPT, RESEARCH_PROMPT, REPORT_PROMPT
from competitive_website_analyst.scoring import compute_overall_score


class BrowserTools(Protocol):
    def screenshot(self) -> bytes: ...
    def click_text(self, text: str) -> dict: ...
    def click_coords(self, x: int, y: int) -> dict: ...
    def wait(self, seconds: float) -> dict: ...
    def extract_metadata(self) -> BrowserMetadata: ...
    def save_screenshot(self, path: str) -> dict: ...


class AgentBackend(Protocol):
    def research(self, domain: str, count: int) -> str: ...
    def drive_browser(self, company: Company, tools: BrowserTools) -> BrowserArtifact: ...
    def analyze(self, artifact: BrowserArtifact) -> str: ...
    def report(self, scorecards: list[Scorecard]) -> str: ...


@dataclass(slots=True)
class MockAgentBackend:
    """Deterministic fallback for local development and tests."""

    def research(self, domain: str, count: int) -> str:
        examples = [
            {"name": "Cursor", "url": "https://cursor.com", "short_description": "AI-first code editor"},
            {"name": "Sourcegraph Cody", "url": "https://sourcegraph.com/cody", "short_description": "AI coding assistant"},
            {"name": "Codeium", "url": "https://codeium.com", "short_description": "AI coding toolkit"},
        ]
        return json.dumps(examples[:count])

    def drive_browser(self, company: Company, tools: BrowserTools) -> BrowserArtifact:
        tools.screenshot()
        tools.wait(1.0)
        tools.save_screenshot("/app/screenshot.png")
        metadata = tools.extract_metadata()
        return BrowserArtifact(
            company=company,
            run_id="",
            status="success",
            screenshot_path="",
            metadata_path="",
            metadata=metadata,
        )

    def analyze(self, artifact: BrowserArtifact) -> str:
        metadata = artifact.metadata or BrowserMetadata()
        ctas = metadata.visible_cta_labels
        nav_items = metadata.nav_items
        scores = ScoreBreakdown(
            positioning_clarity=8 if metadata.h1_hero_text else 5,
            target_audience_clarity=7 if metadata.meta_description else 5,
            cta_strength=7 if ctas else 4,
            visual_polish=7,
            trust_credibility_signals=6 if len(nav_items) >= 3 else 4,
            product_specificity=7 if metadata.h1_hero_text else 4,
            technical_depth=6,
        )
        payload = {
            "company": artifact.company.name,
            "url": str(artifact.company.url),
            "run_id": artifact.run_id,
            "scores": scores.model_dump(),
            "overall_score": compute_overall_score(scores),
            "target_audience_guess": metadata.meta_description[:120],
            "primary_cta": ctas[0] if ctas else "",
            "hero_message": metadata.h1_hero_text,
            "strengths": ["Clear homepage structure"] if metadata.h1_hero_text else [],
            "weaknesses": ["Limited metadata extracted"] if not metadata.meta_description else [],
            "one_sentence_summary": metadata.h1_hero_text or artifact.company.short_description,
        }
        return json.dumps(payload)

    def report(self, scorecards: list[Scorecard]) -> str:
        lines = ["# Competitive Website Analysis", ""]
        for index, card in enumerate(scorecards, start=1):
            lines.append(f"{index}. {card.company} ({card.overall_score})")
        return "\n".join(lines)


class ClaudeAgentSDKBackend:
    """Narrow integration boundary for the real Claude Agent SDK."""

    def research(self, domain: str, count: int) -> str:
        prompt = RESEARCH_PROMPT.format(domain=domain, count=count)
        return self._run_query(
            prompt=prompt,
            max_turns=10,
            allowed_tools=["WebSearch", "WebFetch"],
        )

    def drive_browser(self, company: Company, tools: BrowserTools) -> BrowserArtifact:
        return self._run_browser_agent(company=company, tools=tools)

    def analyze(self, artifact: BrowserArtifact) -> str:
        prompt = ANALYSIS_PROMPT.format(
            company=artifact.company.name,
            url=artifact.company.url,
            metadata=(artifact.metadata or BrowserMetadata()).model_dump_json(),
        )
        screenshot_bytes: bytes | None = None
        if artifact.screenshot_path:
            try:
                screenshot_bytes = open(artifact.screenshot_path, "rb").read()
            except OSError:
                pass
        return self._run_query(prompt=prompt, max_turns=3, image=screenshot_bytes)

    def report(self, scorecards: list[Scorecard]) -> str:
        prompt = REPORT_PROMPT.format(scorecards=json.dumps([card.model_dump(mode="json") for card in scorecards]))
        return self._run_query(prompt=prompt, max_turns=4)

    def _run_query(
        self,
        prompt: str,
        max_turns: int,
        allowed_tools: list[str] | None = None,
        image: bytes | None = None,
    ) -> str:
        return _run_async(
            self._run_query_async(
                prompt=prompt,
                max_turns=max_turns,
                allowed_tools=allowed_tools,
                image=image,
            )
        )

    async def _run_query_async(
        self,
        prompt: str,
        max_turns: int,
        allowed_tools: list[str] | None = None,
        image: bytes | None = None,
    ) -> str:
        query, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage = _import_sdk_symbols(
            "query",
            "ClaudeAgentOptions",
            "AssistantMessage",
            "TextBlock",
            "ResultMessage",
        )
        options = ClaudeAgentOptions(
            system_prompt="Return precise output. If JSON is requested, return JSON only.",
            max_turns=max_turns,
            cwd=os.getcwd(),
            **({"allowed_tools": allowed_tools} if allowed_tools else {}),
        )
        if image is not None:
            image_b64 = base64.b64encode(image).decode("ascii")
            prompt_input: Any = [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64,
                    },
                },
            ]
        else:
            prompt_input = prompt
        assistant_chunks: list[str] = []
        final_result: str | None = None
        async for message in query(prompt=prompt_input, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        assistant_chunks.append(block.text)
            elif isinstance(message, ResultMessage) and message.result:
                final_result = message.result
        text = final_result or "\n".join(part for part in assistant_chunks if part.strip())
        if not text.strip():
            raise RuntimeError("Claude Agent SDK returned no text")
        return text.strip()

    def _run_browser_agent(self, company: Company, tools: BrowserTools) -> BrowserArtifact:
        return _run_async(self._run_browser_agent_async(company=company, tools=tools))

    async def _run_browser_agent_async(self, company: Company, tools: BrowserTools) -> BrowserArtifact:
        (
            tool,
            create_sdk_mcp_server,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            AssistantMessage,
            TextBlock,
            ResultMessage,
        ) = _import_sdk_symbols(
            "tool",
            "create_sdk_mcp_server",
            "ClaudeAgentOptions",
            "ClaudeSDKClient",
            "AssistantMessage",
            "TextBlock",
            "ResultMessage",
        )

        # Track state from tool calls during the agent loop
        agent_state: dict[str, Any] = {
            "screenshot_saved": False,
            "screenshot_path": None,
            "metadata_extracted": False,
            "metadata": None,
        }

        @tool("screenshot", "Capture the current browser viewport", {})
        async def screenshot_tool(_args: dict[str, Any]) -> dict[str, Any]:
            image_b64 = base64.b64encode(tools.screenshot()).decode("ascii")
            return {
                "content": [
                    {"type": "text", "text": "Captured screenshot."},
                    {"type": "image", "data": image_b64, "mimeType": "image/png"},
                ]
            }

        @tool("click_text", "Click text visible on the page", {"text": str})
        async def click_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            result = tools.click_text(str(args["text"]))
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        @tool("click_coords", "Click a coordinate on the page", {"x": int, "y": int})
        async def click_coords_tool(args: dict[str, Any]) -> dict[str, Any]:
            result = tools.click_coords(int(args["x"]), int(args["y"]))
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        @tool("wait", "Wait for the page to settle", {"seconds": float})
        async def wait_tool(args: dict[str, Any]) -> dict[str, Any]:
            result = tools.wait(float(args.get("seconds", 1.0)))
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        @tool("extract_metadata", "Extract metadata from the current page", {})
        async def extract_metadata_tool(_args: dict[str, Any]) -> dict[str, Any]:
            metadata = tools.extract_metadata()
            agent_state["metadata_extracted"] = True
            agent_state["metadata"] = metadata
            return {"content": [{"type": "text", "text": metadata.model_dump_json()}]}

        @tool("save_screenshot", "Save a final full-page screenshot", {"path": str})
        async def save_screenshot_tool(args: dict[str, Any]) -> dict[str, Any]:
            path = str(args["path"])
            result = tools.save_screenshot(path)
            agent_state["screenshot_saved"] = True
            agent_state["screenshot_path"] = path
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        server = create_sdk_mcp_server(
            name="browser",
            version="1.0.0",
            tools=[
                screenshot_tool,
                click_text_tool,
                click_coords_tool,
                wait_tool,
                extract_metadata_tool,
                save_screenshot_tool,
            ],
        )
        allowed_tools = [
            "screenshot",
            "click_text",
            "click_coords",
            "wait",
            "extract_metadata",
            "save_screenshot",
        ]
        options = ClaudeAgentOptions(
            system_prompt="Use the provided browser tools only. Return concise output.",
            max_turns=10,
            cwd=os.getcwd(),
            mcp_servers={"browser": server},
            allowed_tools=allowed_tools,
        )
        prompt = BROWSER_PROMPT.format(company_url=company.url)
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage) and message.is_error:
                    raise RuntimeError(message.result or "browser agent failed")

        # If the agent didn't call save_screenshot, save one now as fallback
        if not agent_state["screenshot_saved"]:
            tools.save_screenshot("/app/screenshot.png")
            agent_state["screenshot_path"] = "/app/screenshot.png"

        # If the agent didn't call extract_metadata, extract now as fallback
        if not agent_state["metadata_extracted"]:
            agent_state["metadata"] = tools.extract_metadata()

        return BrowserArtifact(
            company=company,
            run_id="",
            status="success",
            screenshot_path=agent_state["screenshot_path"],
            metadata_path="/app/metadata.json",
            metadata=agent_state["metadata"],
        )


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Claude Agent SDK calls require a synchronous TensorLake function context")


def _import_sdk_symbols(*names: str):
    try:
        import claude_agent_sdk  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "claude-agent-sdk is required for the real Claude backend. "
            "Install it with `pip install claude-agent-sdk`."
        ) from exc
    return tuple(getattr(claude_agent_sdk, name) for name in names)


def get_agent_backend() -> AgentBackend:
    if os.getenv("COMPETITIVE_ANALYST_USE_MOCKS") == "1":
        return MockAgentBackend()
    return ClaudeAgentSDKBackend()
