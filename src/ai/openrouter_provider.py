import logging
from typing import Dict, Any
import requests

from .base import BaseAIProvider


class OpenRouterProvider(BaseAIProvider):
    """OpenRouter API Provider targeting openai/gpt-oss-20b:free by default."""

    def __init__(self, api_key: str, model_name: str = "openai/gpt-oss-20b:free"):
        self.api_key = api_key.strip()
        self.model_name = model_name.strip()

    def generate_comment(self, video_context: Dict[str, str]) -> str:
        if not self.api_key:
            raise ValueError("OpenRouter API key is empty.")

        title = video_context.get("title", "")
        description = video_context.get("description", "")
        channel = video_context.get("channel", "")

        prompt = (
            "You are a genuine YouTube viewer leaving a short, authentic comment on a video.\n"
            f"Video Title: {title}\n"
            f"Channel: {channel}\n"
            f"Description: {description[:300]}\n\n"
            "Write a natural 1-2 sentence comment related to this video content. "
            "Do NOT use quotes, emojis excess, or generic boilerplate like 'Great video!'. "
            "Return ONLY the comment text."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/yt-tool",
            "X-Title": "YouTube Automation Bot",
        }

        # First attempt via OpenAI SDK if available
        try:
            import openai
            client = openai.OpenAI(
                api_key=self.api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://github.com/yt-tool",
                    "X-Title": "YouTube Automation Bot",
                },
            )
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You write authentic YouTube comments."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=100,
                temperature=0.7,
            )
            if response and response.choices and response.choices[0].message.content:
                return response.choices[0].message.content.strip().strip('"')
        except Exception as exc:
            logging.warning(f"OpenAI SDK call to OpenRouter failed, falling back to direct REST: {exc}")

        # Fallback via direct REST POST request
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "You write authentic YouTube comments."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 100,
            "temperature": 0.7,
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if choices and "message" in choices[0]:
            content = choices[0]["message"].get("content", "")
            if content:
                return content.strip().strip('"')

        raise RuntimeError("No comment content returned from OpenRouter API.")
