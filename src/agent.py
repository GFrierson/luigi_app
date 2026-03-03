import logging
from typing import List, Dict, Optional
from openai import OpenAI
from src.config import get_settings

logger = logging.getLogger(__name__)


def format_schedule_for_prompt(schedules: list[dict]) -> str:
    """Format schedule list for inclusion in the system prompt."""
    if not schedules:
        return "The user has no check-ins configured."
    lines = []
    for s in schedules:
        time_str = f"{s['hour']:02d}:{s['minute']:02d}"
        # Convert to 12-hour format for readability
        hour = s['hour']
        minute = s['minute']
        period = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        status = "active" if s['active'] else "paused"
        lines.append(f"- {display_hour}:{minute:02d} {period} ({status})")
    count = len(schedules)
    return f"The user has {count} check-in(s) configured:\n" + "\n".join(lines)


def get_system_prompt(user_name: str | None, recent_messages: str | None = None, schedule_info: str | None = None) -> str:
    base_prompt = """You are Luigi, a personal health assistant. Your sole purpose is to RECORD health information, not to give advice.

    ## Core Behavior
    - ACKNOWLEDGE what the user shares, briefly and warmly
    - CONFIRM what you understood by restating it back naturally
    - ASK follow-up questions ONLY when information is genuinely ambiguous

    ## Hard Constraints
    - NEVER give health advice, suggestions, or recommendations
    - NEVER say "have you tried...", "you should...", "consider...", or "make sure to..."
    - NEVER suggest remedies, treatments, lifestyle changes, or when to see a doctor
    - If asked for advice, respond: "I'm here to help you track and record, not to give medical advice. What would you like me to note down?"

    ## Follow-up Questions
    Default: Ask sparingly. Only when critical details are missing.
    - GOOD: User says "pain" → "Where is the pain?"
    - GOOD: User says "took my meds" → Confirm it, no follow-up needed
    - BAD: User says "headache since 2pm" → Don't ask "how severe?" unprompted

    If the user says you're asking too many questions, reduce them further.
    If the user asks you to check in on specific things, do so.

    ## Tone
    - Calm, polite, empathetic
    - Concise and straightforward
    - Brief responses (this is messaging, not email)
    - No exclamation points, no excessive warmth

    ## Response Format
    Typical response pattern:
    1. Brief acknowledgment (1 sentence)
    2. Restate what you understood (confirms you heard correctly)
    3. Only ask ONE follow-up if truly necessary

    Example:
    User: "Migraine started around 3pm, took ibuprofen"
    Luigi: "Got it — migraine starting around 3pm, ibuprofen taken. Let me know how you're feeling later."

    NOT: "I'm sorry to hear that! How severe is the pain on a scale of 1-10? Have you been drinking enough water? Remember to rest in a dark room!"
    """

    # Add recent context if provided (for scheduled check-ins)
    context_block = ""
    if recent_messages:
        context_block = f"""
    ## Recent Conversation Context
    Here are recent messages from the past 24 hours. Reference relevant symptoms or medications naturally when checking in, but don't list everything — just what seems most relevant.

    {recent_messages}
    """

    # Add preferred name detection instruction
    preferred_name_block = """
    ## Preferred Name
    If the user asks you to call them a different name (e.g. "call me Nel", "my name is Sam"), acknowledge it warmly and include the tag [PREFERRED_NAME: <name>] at the very end of your response (after the visible text). This tag will be stripped before sending — it is only for internal processing.
    Example: "Of course, I'll call you Nel from now on. [PREFERRED_NAME: Nel]"
    Only include this tag when the user explicitly requests a name change.
    """

    # Add schedule context block
    if schedule_info:
        schedule_context_block = f"""
    ## Current Check-in Schedule
    {schedule_info}
    """
    else:
        schedule_context_block = ""

    # Add schedule management instructions
    schedule_management_block = """
    ## Schedule Management
    When the user requests a change to their check-in schedule, respond naturally AND include exactly one schedule tag at the very end of your response. Tags are stripped before sending — they are for internal processing only.

    Tag formats (use 24-hour HH:MM):
    - Add a check-in: [SCHEDULE_ADD: HH:MM]
    - Remove a check-in: [SCHEDULE_REMOVE: HH:MM]
    - Move a check-in: [SCHEDULE_UPDATE: HH:MM > HH:MM]
    - Pause all check-ins: [SCHEDULE_PAUSE]
    - Resume all check-ins: [SCHEDULE_RESUME]

    Rules:
    - Include at most ONE tag per response
    - Only include a tag when the user explicitly requests a schedule change
    - When user says "stop", do NOT include [SCHEDULE_PAUSE] — that path is handled automatically
    - Use 24-hour format (e.g. 2pm = 14:00, 8am = 08:00)
    - When adding a check-in, acknowledge the specific time they requested

    Examples:
    User: "add a check-in at 2pm" → respond naturally + [SCHEDULE_ADD: 14:00]
    User: "move my morning check-in to 8am" → respond naturally + [SCHEDULE_UPDATE: 10:00 > 08:00]
    User: "remove the afternoon one" → respond naturally + [SCHEDULE_REMOVE: 14:00]
    User: "pause my check-ins" → respond naturally + [SCHEDULE_PAUSE]
    User: "resume check-ins" → respond naturally + [SCHEDULE_RESUME]
    User: "what's my schedule?" → respond with the current schedule, no tag needed
    """

    # Add user-specific block
    if user_name:
        user_block = f"""
    ## This User
    You are speaking with {user_name}. Use their name occasionally but not in every message.
    """
    else:
        user_block = """
    ## New User
    You don't have a name for this user yet. Greet them warmly without asking for their name — you'll learn it naturally if they share it.
    """

    return base_prompt + context_block + preferred_name_block + schedule_context_block + schedule_management_block + user_block


def format_messages_for_context(messages: List[Dict]) -> str:
    """Format message history as readable context for system prompt."""
    if not messages:
        return ""
    lines = []
    for msg in messages:
        role = "User" if msg['direction'] == 'inbound' else "Luigi"
        lines.append(f"{role}: {msg['body']}")
    return "\n".join(lines)


def prepare_conversation_history(conversation_history: List[Dict]) -> List[Dict]:
    """
    Prepare conversation history according to ADR decision:
    Feed the LLM the lesser of:
    - Last 24 hours of messages, OR
    - Last 5 messages
    
    Since conversation_history is already filtered by time window (24 hours)
    when retrieved from the database, we just need to take up to last 5 messages.
    
    Args:
        conversation_history: List of message dicts from database
    Returns:
        Filtered list of up to 5 messages
    """
    # Take up to the last 5 messages
    filtered_history = conversation_history[-5:] if len(conversation_history) > 5 else conversation_history
    logger.debug(f"Prepared {len(filtered_history)} messages from {len(conversation_history)} available")
    return filtered_history

def build_messages(conversation_history: List[Dict], user_name: Optional[str] = None, recent_messages: Optional[str] = None, schedule_info: Optional[str] = None) -> List[Dict]:
    """
    Convert conversation history into OpenAI message format.

    Args:
        conversation_history: List of dicts with keys 'direction', 'body', 'timestamp'
        user_name: Optional user name for personalization
        recent_messages: Optional formatted recent message context for scheduled check-ins
        schedule_info: Optional formatted schedule context for schedule management

    Returns:
        List of message dicts in OpenAI format with 'role' and 'content'
    """
    # First, prepare the history according to ADR rules
    prepared_history = prepare_conversation_history(conversation_history)

    messages = [{"role": "system", "content": get_system_prompt(user_name, recent_messages, schedule_info)}]
    
    for message in prepared_history:
        if message['direction'] == 'inbound':
            role = 'user'
        elif message['direction'] == 'outbound':
            role = 'assistant'
        else:
            logger.warning(f"Unknown direction: {message['direction']}, defaulting to 'user'")
            role = 'user'
        
        messages.append({
            "role": role,
            "content": message['body']
        })
    
    logger.debug(f"Built {len(messages)} messages for LLM")
    return messages

def generate_response(conversation_history: List[Dict], user_name: Optional[str] = None, recent_messages: Optional[str] = None, schedule_info: Optional[str] = None) -> str:
    """
    Generate a response using the LLM.

    Args:
        conversation_history: Recent conversation history
        user_name: Optional user name for personalization
        recent_messages: Optional formatted recent message context for scheduled check-ins
        schedule_info: Optional formatted schedule context for schedule management

    Returns:
        LLM response string or fallback message on error
    """
    config = get_settings()
    try:
        # Initialize OpenAI client with OpenRouter configuration
        client = OpenAI(
            api_key=config.OPENROUTER_API_KEY,
            base_url=config.OPENROUTER_BASE_URL
        )

        # Build messages for the LLM
        messages = build_messages(conversation_history, user_name, recent_messages, schedule_info)
        
        logger.debug(f"Sending request to LLM with model: {config.LLM_MODEL}")
        logger.debug(f"Messages: {messages}")
        
        # Call the LLM
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            max_tokens=300
        )
        
        content = response.choices[0].message.content
        logger.debug(f"Received LLM response: {content}")
        
        return content.strip() if content else "I'm sorry, I didn't get a response. Could you try again?"
        
    except Exception as e:
        logger.error("LLM API call failed", exc_info=True)
        return "The LLM call is failing, I'll try again soon."
