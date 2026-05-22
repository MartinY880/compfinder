"""
rov_agent.py
Calls Claude with the SKILL.md as system prompt, the user's JSON payload,
and the appraisal PDF. Returns parsed structured output.
"""

import base64
import json
import os
from pathlib import Path
from anthropic import Anthropic


def load_system_prompt(skill_path: str = "SKILL.md") -> str:
    """Load the SKILL.md body (strips YAML frontmatter)."""
    text = Path(skill_path).read_text()
    if text.startswith("---"):
        # strip frontmatter
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def encode_pdf(pdf_path: str) -> str:
    return base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("utf-8")


def run_agent(
    payload: dict,
    appraisal_pdf_path: str,
    skill_path: str = "SKILL.md",
    model: str = "claude-sonnet-4-6",
    api_key: str | None = None,
    revision_notes: str | None = None,
    previous_output: dict | None = None,
) -> dict:
    """
    payload: the JSON dict assembled by Streamlit (submission + subject + comps)
    appraisal_pdf_path: path to the uploaded appraisal PDF
    revision_notes: optional user feedback to revise the previous output
    previous_output: the previous agent JSON output to revise
    Returns: parsed dict with form_fields and rebuttal_paragraphs
    """
    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    system_prompt = load_system_prompt(skill_path)
    pdf_b64 = encode_pdf(appraisal_pdf_path)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "Attached is an appraisal PDF. Extract loan number, borrower name(s), "
                        "and subject property address from the PDF. Use the JSON below for the "
                        "date, user summary, and selected comps. Then return the ROV JSON per "
                        "your instructions.\n\n"
                        f"```json\n{json.dumps(payload, separators=(',', ':'))}\n```"
                    ),
                },
            ],
        }
    ]

    # If revision mode, append the previous output as assistant + user feedback
    if revision_notes and previous_output:
        messages.append({
            "role": "assistant",
            "content": f"```json\n{json.dumps(previous_output, indent=2)}\n```",
        })
        messages.append({
            "role": "user",
            "content": (
                "REVISION REQUEST: The user wants changes to the ROV output above. "
                "You MUST regenerate the complete ROV JSON with these changes applied. "
                "The output schema must remain identical (form_fields + rebuttal_paragraphs). "
                "Modify the rebuttal_paragraphs and/or reason field to incorporate the user's feedback. "
                "Return the full updated JSON — do not return a partial response or just the changes.\n\n"
                f"User's revision instructions:\n{revision_notes}"
            ),
        })

    response = client.messages.create(
        model=model,
        max_tokens=4096 if revision_notes else 2048,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
    )

    # Extract text, strip possible code fences, parse JSON
    raw = "".join(block.text for block in response.content if block.type == "text").strip()
    if raw.startswith("```"):
        # remove ```json ... ``` fences if present
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Agent returned non-JSON output:\n{raw[:500]}...") from e
