"""
escalation_agent.py
Calls Claude with a post-ROV appraisal PDF to produce structured escalation JSON
for a UWM Appraisal Deficiency Escalation submission.
"""

import base64
import json
import os
from pathlib import Path

from anthropic import Anthropic


def _load_skill(skill_path: str = "ESCALATION_SKILL.md") -> str:
    """Load the escalation skill file, stripping YAML frontmatter if present."""
    text = Path(skill_path).read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def run_escalation_agent(
    pdf_bytes: bytes,
    loan_number: str = "",
    borrower_name: str = "",
    property_address: str = "",
    rov_agent_json: dict | None = None,
    skill_path: str = "ESCALATION_SKILL.md",
    model: str = "claude-sonnet-4-6",
    api_key: str | None = None,
) -> dict:
    """
    Analyze a post-ROV appraisal PDF and return structured escalation JSON.

    pdf_bytes: raw bytes of the uploaded appraisal PDF
    loan_number / borrower_name / property_address: pre-populated context hints
    skill_path: path to the escalation system prompt file
    Returns: parsed dict matching the ESCALATION_SKILL.md schema
    """
    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    system_prompt = _load_skill(skill_path)
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    context_lines = [
        "Analyze the attached post-ROV appraisal PDF for UWM Appraisal Escalation purposes.",
    ]
    if property_address:
        context_lines.append(f"Property address: {property_address}")
    if loan_number:
        context_lines.append(f"Loan number: {loan_number}")
    if borrower_name:
        context_lines.append(f"Borrower name: {borrower_name}")
    if rov_agent_json:
        context_lines.append(
            "The following is the ROV submission data that was previously sent "
            "to the appraiser. Use this to identify which comps were submitted "
            "and compare against the appraiser's response in the PDF:\n"
            + json.dumps(rov_agent_json, indent=2)
        )
    context_lines.append("Return ONLY valid JSON as specified in your instructions.")

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
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
                        "text": "\n".join(context_lines),
                    },
                ],
            }
        ],
    )

    raw = "".join(block.text for block in response.content if block.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Escalation agent returned non-JSON output:\n{raw[:500]}...") from e
