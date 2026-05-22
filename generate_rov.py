"""
generate_rov.py — The single function Streamlit will call.

Usage:
    from generate_rov import generate_rov_pdf

    output_path = generate_rov_pdf(
        payload=payload_dict,
        appraisal_pdf_path="/tmp/uploaded_appraisal.pdf",
        blank_form_path="Main_ROV_blank.pdf",
        output_path="/tmp/filled_rov.pdf",
    )
"""

from rov_agent import run_agent
from pdf_builder import build_rov_pdf


def generate_rov_pdf(
    payload: dict,
    appraisal_pdf_path: str,
    blank_form_path: str,
    output_path: str,
    skill_path: str = "SKILL.md",
    model: str = "claude-sonnet-4-6",
    api_key: str | None = None,
    revision_notes: str | None = None,
    previous_output: dict | None = None,
) -> dict:
    """
    Full pipeline: JSON + appraisal PDF -> filled ROV PDF.
    Returns a dict with:
      - "pdf_path": where the final PDF was written
      - "agent_output": the parsed JSON Claude returned (useful for logging/debugging)
    """
    agent_output = run_agent(
        payload=payload,
        appraisal_pdf_path=appraisal_pdf_path,
        skill_path=skill_path,
        model=model,
        api_key=api_key,
        revision_notes=revision_notes,
        previous_output=previous_output,
    )
    build_rov_pdf(blank_form_path, agent_output, output_path)
    return {"pdf_path": output_path, "agent_output": agent_output}
