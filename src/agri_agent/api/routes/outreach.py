"""Outreach API — pharma retailer email delivery endpoint.

Called by the platform approval engine (via approval_action) when a human
approves a pharma-outreach propose_action.  Mock implementation for local
testing: logs the email to console and returns success.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agri_agent.api.dependencies import verify_api_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/outreach", tags=["outreach"])


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str


class SendEmailResponse(BaseModel):
    status: str
    to: str
    subject: str


@router.post("/send-email", response_model=SendEmailResponse, dependencies=[Depends(verify_api_key)])
async def send_email(req: SendEmailRequest) -> SendEmailResponse:
    """Send a pharma outreach email (mock: logs to console)."""
    border = "─" * 60
    output = (
        f"\n{'═' * 60}\n"
        f"  📧  OUTREACH EMAIL DISPATCHED\n"
        f"{'═' * 60}\n"
        f"  To      : {req.to}\n"
        f"  Subject : {req.subject}\n"
        f"{border}\n"
        f"{req.body}\n"
        f"{'═' * 60}\n"
    )
    log.info("Mock email sent to %s | subject: %s", req.to, req.subject)
    print(output)
    return SendEmailResponse(status="sent", to=req.to, subject=req.subject)
