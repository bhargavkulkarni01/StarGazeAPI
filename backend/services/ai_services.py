import os
import logging
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Load backend/.env (next to this file) BEFORE reading the key, so it works no matter
# which file imports this module first. .env wins over stale shell variables.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
SYSTEM_PROMPT = (
    "You tell the mythological story behind a constellation. "
    "There may be many stars in the photo; mention only the ones that have important "
    "story details to tell, weaving them in where they fit naturally. "
    "Start with a short title line, then write about 100 words in an engaging, storyteller tone. "
    "Mention the origin culture (e.g. Indian, Roman, Greek, Arabic, Polynesian)."
)


class AnthropicAI:

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is empty or missing in backend/.env")
        # Only needed for organization-scoped keys; leave unset for workspace keys.
        workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
        self.client = anthropic.Anthropic(
            api_key=api_key,
            default_headers=(
                {"anthropic-workspace-id": workspace_id} if workspace_id else None
            ),
            timeout=30,
        )

    def get_story(self, constellation: str, star_names: list[str]) -> str | None:
        stars = ", ".join(star_names) if star_names else "none identified"
        try:
            msg = self.client.messages.create(
                model=MODEL,
                max_tokens=2000,  # Haiku 5.5 thinks by default; thinking counts toward this cap
                output_config={"effort": "low"},
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"Constellation: {constellation}. Objects identified in the photo: {stars}.",
                }],
            )
        except anthropic.APIError as e:
            log.error("Anthropic API error: %s", e)
            return None
        if msg.stop_reason != "end_turn":
            log.warning("Story stopped early: stop_reason=%s (request %s)", msg.stop_reason, msg._request_id)
        return "".join(b.text for b in msg.content if b.type == "text") or None
