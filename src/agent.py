import logging
from typing import List, Dict
from openai import OpenAI
from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Luigi, a personal health assistant for Shanelle. 
Your tone is calm, polite, empathetic, concise, and straightforward.
You help Shanelle track her symptoms, medications, and general wellbeing.
Ask clarifying questions when uncertain.
Keep responses brief—this is SMS, not email."""

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

def build_messages(conversation_history: List[Dict]) -> List[Dict]:
    """
    Convert conversation history into OpenAI message format.
    
    Args:
        conversation_history: List of dicts with keys 'direction', 'body', 'timestamp'
        
    Returns:
        List of message dicts in OpenAI format with 'role' and 'content'
    """
    # First, prepare the history according to ADR rules
    prepared_history = prepare_conversation_history(conversation_history)
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
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

def generate_response(conversation_history: List[Dict]) -> str:
    """
    Generate a response using the LLM.
    
    Args:
        conversation_history: Recent conversation history
        
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
        messages = build_messages(conversation_history)
        
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
