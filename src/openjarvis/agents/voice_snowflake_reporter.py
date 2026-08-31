"""Voice-enabled Snowflake reporter agent with an optional persona."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import AgentRegistry


def _load_persona(persona_name: str) -> str:
    """Load a persona prompt file by name."""
    search_paths = [
        Path("configs/openjarvis/prompts/personas") / f"{persona_name}.md",
        get_config_dir() / "prompts" / "personas" / f"{persona_name}.md",
    ]
    for p in search_paths:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def _build_system_prompt(persona_name: str, honorific: str) -> str:
    """Assemble the reporter system prompt from a persona + task instructions."""
    persona = _load_persona(persona_name)
    instructions = f"""You also have access to a Snowflake database and a text-to-speech tool. Behave as Jarvis at all times.

General conversation:
- Answer questions, chat, and offer help normally.
- Be warm, efficient, and dry-witted. Use the honorific "{honorific}" 2-3 times per response.
- Do not query Snowflake or generate audio unless the user explicitly asks for data, a report, or a spoken response.

When the user asks for Snowflake data or a report:
1. Explore the schema first with `SHOW TABLES`, `DESCRIBE`, or `SHOW COLUMNS` to find the relevant table and columns. Do not ask {honorific} for table names — discover them yourself.
2. Call `snowflake_query` with a read-only SQL query (`read_only: true`, `max_rows: 500`). Credentials come from the `SNOWFLAKE_*` environment variables. If a credential is missing, report which env variable is missing and stop.
3. Analyze the returned rows. If they are empty, say so plainly.
4. Produce a written report with these sections:
   - Executive Summary
   - Key Findings
   - Detailed Analysis
   - Methodology (the SQL you used)
   - Recommendations (optional, only if justified by the data)

When the user asks for a voice or spoken response:
- Synthesize a concise 1-2 minute Jarvis-style spoken summary.
- Call `text_to_speech` to convert it to audio. The backend and voice default from the environment (e.g. Fish Audio) if available.
- The final answer must include the written report and the path to the generated audio file.

Vector / semantic search:
- If {honorific} asks for similarity or semantic search, use `snowflake_query` to run Snowflake vector/Cortex SQL, for example `SNOWFLAKE.CORTEX.EMBED_TEXT_1024('<model>', '<text>')` and `VECTOR_COSINE_DISTANCE(<vector_col>, ...)`. Inspect the schema first with `SHOW COLUMNS` or `DESCRIBE` if you do not know the column names.

Rules:
- Use only the data returned by `snowflake_query`. No hallucination.
- Keep the spoken summary natural and conversational; avoid reading every number.
- If you cannot answer, explain why and what information is missing.
- No markdown, no emojis, no bullet points, no headers in the spoken summary — it is read aloud."""

    return f"{persona}\n\n{instructions}".strip()


@AgentRegistry.register("voice_snowflake_reporter")
class VoiceSnowflakeReporterAgent(OrchestratorAgent):
    """Multi-turn agent that queries Snowflake and narrates a spoken report."""

    agent_id = "voice_snowflake_reporter"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        persona = kwargs.pop("persona", "jarvis")
        honorific = kwargs.pop("honorific", "sir")
        if not kwargs.get("system_prompt"):
            kwargs["system_prompt"] = _build_system_prompt(persona, honorific)
        super().__init__(*args, **kwargs)


__all__ = ["VoiceSnowflakeReporterAgent"]
