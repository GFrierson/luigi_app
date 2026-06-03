import asyncio
import logging
import os
import re
from dataclasses import dataclass

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from src.config import get_settings
from src.database import (
    init_db, seed_default_schedules, insert_message, get_recent_messages,
    get_user_db_path, set_telegram_name, get_display_name, set_preferred_name,
    deactivate_all_schedules, get_all_schedules, add_schedule, remove_schedule,
    update_schedule_time, reactivate_all_schedules,
    get_all_medications, get_all_active_medication_groups, find_medication_group,
    get_medications_by_group, log_group_events, get_medication_by_name,
    log_medication_event, create_medication_group, create_medication,
)
from src.agent import generate_response, format_schedule_for_prompt, extract_medication_action, get_medication_state_context
from src.medical.ingestion import (
    ingest_document,
    handle_photo_group,
    commit_ingestion,
    handle_correction,
    _pending_confirmations,
)
from src.medical.confirmation import parse_confirmation_reply
from src.medical.queries import (
    get_global_obligations,
    get_member_holds_overdue,
    get_readjudicated_claims,
)

logger = logging.getLogger(__name__)


async def send_message(chat_id: int, text: str) -> int:
    """
    Send a message to a Telegram chat.

    Returns:
        The message_id of the sent message

    Raises:
        Exception: If the Telegram API call fails
    """
    config = get_settings()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    try:
        message = await bot.send_message(chat_id=chat_id, text=text)
        message_id = message.message_id
        logger.info(f"Telegram message sent successfully with ID: {message_id}")
        return message_id

    except Exception as e:
        logger.error(f"Failed to send Telegram message: {str(e)}", exc_info=True)
        raise


async def start_command(update: Update, context) -> None:
    """Handle the /start command — log chat_id and send welcome message."""
    chat_id = update.message.chat_id
    logger.info(f"New user started bot: chat_id={chat_id}")
    await update.message.reply_text(
        "Hi! I'm Luigi, your health tracker. Send me a message to get started!"
    )


def _extract_preferred_name_tag(response_text: str) -> tuple[str, str | None]:
    """
    Parse [PREFERRED_NAME: X] tag from LLM response.

    Returns (cleaned_text, name_or_None).
    """
    import re
    match = re.search(r'\[PREFERRED_NAME:\s*(.+?)\]', response_text)
    if match:
        name = match.group(1).strip()
        cleaned = re.sub(r'\s*\[PREFERRED_NAME:\s*.+?\]', '', response_text).strip()
        return cleaned, name
    return response_text, None


@dataclass
class ScheduleAction:
    action: str  # ADD, REMOVE, UPDATE, PAUSE, RESUME
    hour: int | None = None
    minute: int | None = None
    new_hour: int | None = None
    new_minute: int | None = None


def _validate_schedule_time(hour: int, minute: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _generate_template_for_time(hour: int, minute: int) -> str:
    if 5 <= hour < 12:
        return "Good morning! How are you feeling today?"
    elif 12 <= hour < 17:
        return "Afternoon check-in: How are you doing?"
    else:
        return "Evening check-in: How was your day? Any symptoms or notes to share?"


def _extract_schedule_tag(response_text: str) -> tuple[str, ScheduleAction | None]:
    """
    Parse one schedule tag from LLM response.
    Returns (cleaned_text, action_or_None).
    """
    # SCHEDULE_UPDATE: HH:MM > HH:MM
    match = re.search(r'\[SCHEDULE_UPDATE:\s*(\d{1,2}):(\d{2})\s*>\s*(\d{1,2}):(\d{2})\]', response_text)
    if match:
        old_h, old_m, new_h, new_m = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        cleaned = re.sub(r'\s*\[SCHEDULE_UPDATE:[^\]]+\]', '', response_text).strip()
        if _validate_schedule_time(old_h, old_m) and _validate_schedule_time(new_h, new_m):
            return cleaned, ScheduleAction(action="UPDATE", hour=old_h, minute=old_m, new_hour=new_h, new_minute=new_m)
        return cleaned, None

    # SCHEDULE_ADD: HH:MM
    match = re.search(r'\[SCHEDULE_ADD:\s*(\d{1,2}):(\d{2})\]', response_text)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        cleaned = re.sub(r'\s*\[SCHEDULE_ADD:[^\]]+\]', '', response_text).strip()
        if _validate_schedule_time(h, m):
            return cleaned, ScheduleAction(action="ADD", hour=h, minute=m)
        return cleaned, None

    # SCHEDULE_REMOVE: HH:MM
    match = re.search(r'\[SCHEDULE_REMOVE:\s*(\d{1,2}):(\d{2})\]', response_text)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        cleaned = re.sub(r'\s*\[SCHEDULE_REMOVE:[^\]]+\]', '', response_text).strip()
        if _validate_schedule_time(h, m):
            return cleaned, ScheduleAction(action="REMOVE", hour=h, minute=m)
        return cleaned, None

    # SCHEDULE_PAUSE
    match = re.search(r'\[SCHEDULE_PAUSE\]', response_text)
    if match:
        cleaned = re.sub(r'\s*\[SCHEDULE_PAUSE\]', '', response_text).strip()
        return cleaned, ScheduleAction(action="PAUSE")

    # SCHEDULE_RESUME
    match = re.search(r'\[SCHEDULE_RESUME\]', response_text)
    if match:
        cleaned = re.sub(r'\s*\[SCHEDULE_RESUME\]', '', response_text).strip()
        return cleaned, ScheduleAction(action="RESUME")

    return response_text, None


async def _execute_schedule_action(action: ScheduleAction, chat_id: int, db_path: str, scheduler) -> None:
    """Execute DB + scheduler changes for a parsed schedule action."""
    from src.scheduler import add_user_job, remove_user_job, remove_all_user_jobs, schedule_user_check_ins

    if action.action == "ADD":
        template = _generate_template_for_time(action.hour, action.minute)
        result = await asyncio.to_thread(add_schedule, db_path, action.hour, action.minute, template)
        if result and scheduler:
            add_user_job(scheduler, chat_id, db_path, action.hour, action.minute, template)
            logger.info(f"Added check-in at {action.hour:02d}:{action.minute:02d} for user {chat_id}")

    elif action.action == "REMOVE":
        removed = await asyncio.to_thread(remove_schedule, db_path, action.hour, action.minute)
        if removed and scheduler:
            remove_user_job(scheduler, chat_id, action.hour, action.minute)
            logger.info(f"Removed check-in at {action.hour:02d}:{action.minute:02d} for user {chat_id}")

    elif action.action == "UPDATE":
        updated = await asyncio.to_thread(
            update_schedule_time, db_path, action.hour, action.minute, action.new_hour, action.new_minute
        )
        if updated and scheduler:
            remove_user_job(scheduler, chat_id, action.hour, action.minute)
            # Fetch the template from DB for the new time
            all_schedules = await asyncio.to_thread(get_all_schedules, db_path)
            new_sched = next((s for s in all_schedules if s['hour'] == action.new_hour and s['minute'] == action.new_minute), None)
            if new_sched:
                add_user_job(scheduler, chat_id, db_path, action.new_hour, action.new_minute, new_sched['message_template'])
            logger.info(f"Updated check-in from {action.hour:02d}:{action.minute:02d} to {action.new_hour:02d}:{action.new_minute:02d} for user {chat_id}")

    elif action.action == "PAUSE":
        await asyncio.to_thread(deactivate_all_schedules, db_path)
        if scheduler:
            remove_all_user_jobs(scheduler, chat_id)
        logger.info(f"Paused all check-ins for user {chat_id}")

    elif action.action == "RESUME":
        await asyncio.to_thread(reactivate_all_schedules, db_path)
        if scheduler:
            schedule_user_check_ins(scheduler, chat_id, db_path)
        logger.info(f"Resumed all check-ins for user {chat_id}")


def _process_medication_action(action: dict, db_path: str, chat_id: int, scheduler=None) -> None:
    """
    Dispatch a medication action extracted by Call 2 to the appropriate DB writes.
    Never raises — all errors are caught and logged.
    """
    try:
        action_type = action.get("action", "none")

        if action_type == "log_group":
            group_name = action.get("group_name", "")
            group = find_medication_group(db_path, group_name)
            if not group:
                logger.warning(f"log_group: group '{group_name}' not found in DB for chat {chat_id}")
                return
            meds = get_medications_by_group(db_path, group['id'])
            if not meds:
                logger.debug(f"log_group: no active meds in group '{group_name}'")
                return

            skipped_names = [n.lower() for n in action.get("skipped", [])]
            taken_ids = [m['id'] for m in meds if m['name'].lower() not in skipped_names]
            skipped_ids = [m['id'] for m in meds if m['name'].lower() in skipped_names]
            log_group_events(db_path, group['id'], taken_ids, skipped_ids)
            logger.info(f"Logged group event for '{group_name}': {len(taken_ids)} taken, {len(skipped_ids)} skipped")

        elif action_type == "log_single":
            med_name = action.get("medication_name", "")
            status = action.get("status", "taken")
            med = get_medication_by_name(db_path, med_name)
            if not med:
                logger.warning(f"log_single: medication '{med_name}' not found for chat {chat_id}")
                return
            log_medication_event(db_path, med['id'], status)
            logger.info(f"Logged single event for '{med_name}': {status}")

        elif action_type == "create_group":
            from src.scheduler import register_medication_reminder_job
            group_name = action.get("name", "")
            aliases = action.get("aliases")
            hour = action.get("schedule_hour")
            minute = action.get("schedule_minute")
            group_id = create_medication_group(db_path, group_name, aliases, hour, minute)
            logger.info(f"Created medication group '{group_name}' (id={group_id}) for chat {chat_id}")
            if hour is not None and minute is not None and scheduler:
                register_medication_reminder_job(scheduler, chat_id, db_path, group_id, group_name, hour, minute)

        elif action_type == "add_medication":
            med_name = action.get("name", "")
            dosage = action.get("dosage")
            med_type = action.get("type", "scheduled")
            group_name = action.get("group_name")
            group_id = None
            if group_name:
                group = find_medication_group(db_path, group_name)
                if group:
                    group_id = group['id']
            create_medication(db_path, med_name, dosage, med_type, group_id)
            logger.info(f"Added medication '{med_name}' to group '{group_name}' for chat {chat_id}")

        elif action_type == "modify_medication":
            # Not yet implemented
            logger.info(f"Medication action 'modify_medication' not yet implemented for chat {chat_id}")

        elif action_type == "none":
            pass

        else:
            logger.debug(f"Unknown medication action type '{action_type}' for chat {chat_id}")

    except Exception:
        logger.error(f"_process_medication_action failed for chat {chat_id}", exc_info=True)


async def handle_message(chat_id: int, text: str, message_id: int, scheduler=None, telegram_first_name: str = None) -> str:
    """
    Process an incoming message and generate a response.

    This is the core message handling logic: per-user DB init, inbound logging,
    stop-command handling, name extraction, LLM response, and outbound logging.

    Args:
        chat_id: Telegram chat ID
        text: Message text from user
        message_id: Telegram message ID
        scheduler: Optional AsyncIOScheduler for managing scheduled jobs

    Returns:
        The response text sent to the user
    """
    config = get_settings()

    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    is_new_user = not os.path.exists(db_path)

    # Always init so ALTER TABLE migration runs for existing users
    init_db(db_path)

    if is_new_user:
        seed_default_schedules(db_path)
        logger.info(f"Created new database for user {chat_id}")
        if scheduler:
            from src.scheduler import schedule_check_ins
            schedule_check_ins(scheduler)

    # Store Telegram identity
    if telegram_first_name:
        set_telegram_name(db_path, telegram_first_name)

    insert_message(db_path, 'inbound', text, message_id)

    if text.strip().lower() == "stop":
        await asyncio.to_thread(deactivate_all_schedules, db_path)

        if scheduler:
            from src.scheduler import remove_all_user_jobs
            remove_all_user_jobs(scheduler, chat_id)

        response_text = "All scheduled check-ins have been paused. Just say 'resume' or ask me to start checking in again whenever you're ready."
        sent_id = await send_message(chat_id, response_text)
        insert_message(db_path, 'outbound', response_text, sent_id)
        logger.info(f"User {chat_id} requested to stop schedules")

    else:
        display_name = get_display_name(db_path)

        # Fetch schedule context for the LLM
        all_schedules = await asyncio.to_thread(get_all_schedules, db_path)
        schedule_info = format_schedule_for_prompt(all_schedules)

        # Fetch medication state for the LLM (also used by Call 2 below)
        medications = get_all_medications(db_path)
        groups = get_all_active_medication_groups(db_path)
        med_state = get_medication_state_context(medications, groups)

        history = get_recent_messages(db_path, limit=5, hours=24)
        response_text = generate_response(history, display_name, schedule_info=schedule_info, med_state=med_state)

        # Parse preferred name tag from LLM response
        response_text, preferred_name = _extract_preferred_name_tag(response_text)
        if preferred_name:
            set_preferred_name(db_path, preferred_name)
            logger.info(f"Saved preferred name for {chat_id}: {preferred_name}")

        # Parse and execute schedule tag from LLM response
        response_text, schedule_action = _extract_schedule_tag(response_text)
        if schedule_action:
            await _execute_schedule_action(schedule_action, chat_id, db_path, scheduler)

        sent_id = await send_message(chat_id, response_text)
        insert_message(db_path, 'outbound', response_text, sent_id)
        logger.info(f"Sent response to chat {chat_id} with message ID: {sent_id}")

        # Call 2: extract medication action (best-effort, never blocks response)
        try:
            action = extract_medication_action(text, response_text, med_state)
            _process_medication_action(action, db_path, chat_id, scheduler=scheduler)
        except Exception:
            logger.error(f"Medication extraction pipeline failed for chat {chat_id}", exc_info=True)

    return response_text


async def schedule_command(update: Update, context) -> None:
    """Handle /schedule command — display the user's current check-in schedule."""
    chat_id = update.message.chat_id
    config = get_settings()
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)

    if not os.path.exists(db_path):
        await update.message.reply_text("No schedule found. Send a message to get started!")
        return

    all_schedules = get_all_schedules(db_path)
    if not all_schedules:
        await update.message.reply_text("You have no check-ins configured.")
        return

    lines = ["Your check-in schedule:"]
    for s in all_schedules:
        hour = s['hour']
        minute = s['minute']
        period = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        status = "active" if s['active'] else "paused"
        lines.append(f"  {display_hour}:{minute:02d} {period} — {status}")

    await update.message.reply_text("\n".join(lines))


async def balance_command(update: Update, context) -> None:
    """Handle /balance command — show outstanding net obligation grouped by practice."""
    chat_id = update.effective_chat.id
    config = get_settings()
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    init_db(db_path)

    rows = await asyncio.to_thread(get_global_obligations, db_path)

    # Group by billing_practice_id and sum net_obligation per practice.
    # Keep practice_name from the first row of each group.
    by_practice: dict[int, dict] = {}
    for r in rows:
        pid = r.get("billing_practice_id")
        if pid is None:
            continue
        existing = by_practice.get(pid)
        if existing is None:
            by_practice[pid] = {
                "practice_name": r.get("practice_name") or "Unknown practice",
                "net": float(r.get("net_obligation") or 0.0),
            }
        else:
            existing["net"] += float(r.get("net_obligation") or 0.0)

    # Filter to practices with positive outstanding balance only.
    outstanding = [
        (info["practice_name"], info["net"])
        for info in by_practice.values()
        if info["net"] > 0
    ]

    if not outstanding:
        await update.message.reply_text("No outstanding balance.")
        return

    outstanding.sort(key=lambda x: x[0].lower())
    total = sum(net for _, net in outstanding)
    lines = ["Outstanding balance:"]
    for name, net in outstanding:
        lines.append(f"  {name}: ${net:.2f}")
    lines.append(f"Total: ${total:.2f}")
    await update.message.reply_text("\n".join(lines))


async def pending_command(update: Update, context) -> None:
    """Handle /pending command — list member-held insurer payments overdue >7 days."""
    chat_id = update.effective_chat.id
    config = get_settings()
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    init_db(db_path)

    rows = await asyncio.to_thread(get_member_holds_overdue, db_path, 7)

    if not rows:
        await update.message.reply_text("No member-held payments pending forwarding.")
        return

    lines = ["Pending member-held payments:"]
    for r in rows:
        practice = r.get("practice_name") or "Unknown practice"
        amount = float(r.get("held_amount") or 0.0)
        days = float(r.get("days_held") or 0.0)
        lines.append(f"  {practice} — ${amount:.2f} (held {days:.0f} days)")
    await update.message.reply_text("\n".join(lines))


async def readjudications_command(update: Update, context) -> None:
    """Handle /readjudications command — list claims whose current adjudication is revision > 1."""
    chat_id = update.effective_chat.id
    config = get_settings()
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    init_db(db_path)

    rows = await asyncio.to_thread(get_readjudicated_claims, db_path)

    if not rows:
        await update.message.reply_text("No re-adjudications on record.")
        return

    lines = ["Re-adjudicated claims:"]
    for r in rows:
        service_date = r.get("service_date") or "unknown date"
        practice = r.get("practice_name") or "Unknown practice"
        revision = r.get("revision") or 0
        lines.append(f"  {service_date} · {practice} · revision {revision}")
    await update.message.reply_text("\n".join(lines))


_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # Telegram Bot API limit


async def _on_document(update: Update, context) -> None:
    """Handler for incoming Telegram document messages (PDF, etc.)."""
    if not update.message or not update.message.document:
        return

    chat_id = update.message.chat_id
    doc = update.message.document

    if doc.file_size and doc.file_size > _MAX_UPLOAD_BYTES:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "That file is over 20 MB (Telegram API limit). "
                "Please compress it or split into pages."
            ),
        )
        return

    config = get_settings()
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    init_db(db_path)

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        ba = await tg_file.download_as_bytearray()
        file_bytes = bytes(ba)
    except Exception:
        logger.error(
            f"_on_document: failed to download file for chat {chat_id}",
            exc_info=True,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="I couldn't download that file from Telegram. Please try again.",
        )
        return

    result = await ingest_document(
        db_path,
        config.DOCUMENTS_DIR,
        chat_id,
        file_bytes,
        doc.file_name or "document",
        doc.mime_type or "application/octet-stream",
        context,
    )
    await context.bot.send_message(chat_id=chat_id, text=result)


async def _on_photo(update: Update, context) -> None:
    """Handler for incoming Telegram photo messages."""
    if not update.message or not update.message.photo:
        return

    chat_id = update.message.chat_id
    photo = update.message.photo[-1]  # largest size

    if photo.file_size and photo.file_size > _MAX_UPLOAD_BYTES:
        await context.bot.send_message(
            chat_id=chat_id,
            text="That photo is over 20 MB. Please send a smaller version.",
        )
        return

    config = get_settings()
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    init_db(db_path)

    try:
        tg_file = await context.bot.get_file(photo.file_id)
        ba = await tg_file.download_as_bytearray()
        file_bytes = bytes(ba)
    except Exception:
        logger.error(
            f"_on_photo: failed to download photo for chat {chat_id}",
            exc_info=True,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="I couldn't download that photo from Telegram. Please try again.",
        )
        return

    media_group_id = update.message.media_group_id
    if media_group_id:
        await handle_photo_group(
            chat_id,
            media_group_id,
            file_bytes,
            db_path,
            config.DOCUMENTS_DIR,
            context,
        )
    else:
        result = await ingest_document(
            db_path,
            config.DOCUMENTS_DIR,
            chat_id,
            file_bytes,
            "photo.jpg",
            "image/jpeg",
            context,
        )
        await context.bot.send_message(chat_id=chat_id, text=result)


async def _on_message(update: Update, context) -> None:
    """python-telegram-bot handler: extracts fields and delegates to handle_message."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text.strip()
    message_id = update.message.message_id

    if not text:
        return

    # Confirmation intercept: if there's a pending document confirmation for
    # this chat, route the reply through parse_confirmation_reply first.
    if chat_id in _pending_confirmations:
        config = get_settings()
        db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
        pending = _pending_confirmations[chat_id]
        result = parse_confirmation_reply(text, pending.get("pending_items", []))
        action = result.get("action")
        if action == "confirm":
            try:
                await commit_ingestion(db_path, chat_id, pending)
            except Exception:
                logger.error(
                    f"_on_message: commit_ingestion failed for chat {chat_id}",
                    exc_info=True,
                )
            _pending_confirmations.pop(chat_id, None)
            try:
                await context.bot.send_message(chat_id=chat_id, text="Saved.")
            except Exception:
                logger.error(
                    f"_on_message: failed to send 'Saved.' to chat {chat_id}",
                    exc_info=True,
                )
            return
        elif action == "correction":
            try:
                await handle_correction(db_path, chat_id, pending, result, context)
            except Exception:
                logger.error(
                    f"_on_message: handle_correction failed for chat {chat_id}",
                    exc_info=True,
                )
            return
        elif action == "cancel":
            _pending_confirmations.pop(chat_id, None)
            try:
                await context.bot.send_message(chat_id=chat_id, text="Cancelled.")
            except Exception:
                logger.error(
                    f"_on_message: failed to send 'Cancelled.' to chat {chat_id}",
                    exc_info=True,
                )
            return
        # free_text or unknown: fall through to normal handle_message

    telegram_first_name = None
    if update.message.from_user:
        telegram_first_name = update.message.from_user.first_name

    logger.info(f"Received message from chat {chat_id}: {text}")

    scheduler = context.bot_data.get("scheduler")
    try:
        await handle_message(chat_id, text, message_id, scheduler=scheduler, telegram_first_name=telegram_first_name)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)


def create_application(token: str) -> Application:
    """Build and return an Application with all message handlers registered."""
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("pending", pending_command))
    application.add_handler(CommandHandler("readjudications", readjudications_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    application.add_handler(MessageHandler(filters.Document.ALL, _on_document))
    application.add_handler(MessageHandler(filters.PHOTO, _on_photo))
    return application
