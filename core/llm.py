import openai
from mistralai import Mistral

from core.config import settings
from core.logger import log


def get_deepseek_client() -> openai.OpenAI:
    return openai.OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )


def chat_with_fallback(messages: list[dict], model: str | None = None, timeout: int = 15) -> str:
    """
    Tries DeepSeek first. Falls back to Mistral on 429 or timeout.
    Returns the raw string content of the assistant message.
    """
    try:
        client = get_deepseek_client()
        response = client.chat.completions.create(
            model=model or settings.deepseek_model,
            messages=messages,
            timeout=timeout,
        )
        return response.choices[0].message.content
    except (openai.RateLimitError, openai.APITimeoutError) as e:
        log.warning(f"DeepSeek failed ({e}), switching to Mistral fallback")
        mistral = Mistral(api_key=settings.mistral_api_key)
        response = mistral.chat.complete(
            model=settings.mistral_model,
            messages=messages,
        )
        return response.choices[0].message.content
