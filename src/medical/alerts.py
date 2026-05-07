"""
Medical billing alerts (Phase 5).

Daily / monthly proactive notifications dispatched via APScheduler jobs:
- send_readjudication_alerts: per-event alert when a claim was re-adjudicated today
- send_member_holds_nudge: daily nudge when insurer→member payments are >7 days old
- send_monthly_summary: monthly digest on the 1st of each month

All functions are async, accept (db_path, chat_id), never raise — exceptions are
caught and logged with exc_info=True. Caller (the scheduler) iterates user DBs.
"""

import asyncio
import calendar
import logging
from datetime import date

from src.medical.queries import (
    get_global_obligations,
    get_member_holds_overdue,
    get_readjudicated_claims,
    get_recent_readjudication_events,
)
from src.telegram_handler import send_message

logger = logging.getLogger(__name__)


async def send_readjudication_alerts(db_path: str, chat_id: int) -> None:
    """Send one Telegram message per re-adjudication event that occurred today."""
    try:
        events = await asyncio.to_thread(get_recent_readjudication_events, db_path)
        if not events:
            return

        # Cross-reference each event's claim_id with the readjudicated-claims
        # view to enrich the alert with practice_name + service_date + revision.
        claims_data = await asyncio.to_thread(get_readjudicated_claims, db_path)
        by_claim = {row["claim_id"]: row for row in claims_data}

        for event in events:
            claim_id = event.get("claim_id")
            claim_row = by_claim.get(claim_id)
            if not claim_row:
                logger.debug(
                    f"send_readjudication_alerts: no claim row for claim_id={claim_id}, "
                    f"skipping event id={event.get('id')}"
                )
                continue

            practice_name = claim_row.get("practice_name") or "Unknown practice"
            service_date = claim_row.get("service_date") or "unknown date"
            revision = claim_row.get("revision") or "?"
            text = (
                f"Re-adjudication alert: {practice_name} claim ({service_date}) "
                f"has been revised to revision {revision}."
            )
            try:
                await send_message(chat_id, text)
            except Exception:
                logger.error(
                    f"send_readjudication_alerts: failed to send alert for "
                    f"claim_id={claim_id} chat_id={chat_id}",
                    exc_info=True,
                )
    except Exception:
        logger.error(
            f"send_readjudication_alerts: top-level failure for chat_id={chat_id}",
            exc_info=True,
        )


async def send_member_holds_nudge(db_path: str, chat_id: int) -> None:
    """Send a single nudge if there are any member-held payments overdue >7 days."""
    try:
        holds = await asyncio.to_thread(get_member_holds_overdue, db_path, 7)
        if not holds:
            return

        text = (
            f"Reminder: you have {len(holds)} payment(s) held that should be "
            f"forwarded to your provider — use /pending for details."
        )
        try:
            await send_message(chat_id, text)
        except Exception:
            logger.error(
                f"send_member_holds_nudge: failed to send nudge to chat_id={chat_id}",
                exc_info=True,
            )
    except Exception:
        logger.error(
            f"send_member_holds_nudge: top-level failure for chat_id={chat_id}",
            exc_info=True,
        )


async def send_monthly_summary(db_path: str, chat_id: int) -> None:
    """Send a monthly digest of outstanding balance, held payments, and re-adjudications."""
    try:
        today = date.today()
        month_name = calendar.month_name[today.month]
        year = today.year

        obligations = await asyncio.to_thread(get_global_obligations, db_path)
        holds = await asyncio.to_thread(get_member_holds_overdue, db_path, 0)
        readj = await asyncio.to_thread(get_readjudicated_claims, db_path)

        total_obligation = sum(
            float(r.get("net_obligation") or 0.0) for r in obligations
        )
        total_held = sum(float(r.get("held_amount") or 0.0) for r in holds)

        text = (
            f"Monthly medical billing summary ({month_name} {year}):\n"
            f"• Outstanding balance: ${total_obligation:.2f}\n"
            f"• Payments held by insurer: ${total_held:.2f}\n"
            f"• Re-adjudicated claims: {len(readj)}\n"
            f"\n"
            f"Use /balance, /pending, and /readjudications for details."
        )
        try:
            await send_message(chat_id, text)
        except Exception:
            logger.error(
                f"send_monthly_summary: failed to send summary to chat_id={chat_id}",
                exc_info=True,
            )
    except Exception:
        logger.error(
            f"send_monthly_summary: top-level failure for chat_id={chat_id}",
            exc_info=True,
        )
