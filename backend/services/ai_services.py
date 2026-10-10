import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Load backend/.env (next to this file) BEFORE reading the key, so it works no matter
# which file imports this module first. .env wins over stale shell variables.
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

MODEL = "claude-haiku-4-5-20251001"


def get_story(constellation: str, star_names: list[str]) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is empty or missing in backend/.env")

    # Only needed for organization-scoped keys; leave unset for workspace keys.
    workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(
        api_key=api_key,
        default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None,
    )

    stars = ", ".join(star_names) if star_names else "none identified"
    msg = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": (
                f"Tell me the mythological story behind the constellation {constellation}. "
                f"Objects identified in the photo: {stars}. "
                "There may be many stars in the photo; keep only the ones that have important "
                "story details to tell. "
                "Start with a short title line, then write about 100 words in an engaging, "
                "storyteller tone. Mention the origin culture (e.g. Indian, Roman, Greek, "
                "Arabic, Polynesian), and weave in any identified stars where they fit naturally."
            ),
        }],
    )
    return "".join(b.text for b in msg.content if b.type == "text")