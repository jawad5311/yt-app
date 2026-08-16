import logging
from typing import Dict, Any
import requests

from .base import BaseAIProvider


class OpenRouterProvider(BaseAIProvider):
    """OpenRouter API Provider targeting openai/gpt-oss-20b:free by default with fallback models."""

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

        # Models list with fallback to free openrouter models
        models_to_try = [self.model_name, "google/gemini-2.0-flash-lite-preview-02-05:free", "meta-llama/llama-3.3-70b-instruct:free"]

        for model in models_to_try:
            # 1. SDK Try
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
                    model=model,
                    messages=[
                        {"role": "system", "content": "You write authentic YouTube comments."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=100,
                    temperature=0.7,
                )
                if response and response.choices and response.choices[0].message:
                    content = response.choices[0].message.content
                    if isinstance(content, str) and content.strip():
                        return content.strip().strip('"')
            except Exception as exc:
                logging.debug(f"OpenRouter SDK attempt for {model} failed: {exc}")

            # 2. REST Fallback
            try:
                url = "https://openrouter.ai/api/v1/chat/completions"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You write authentic YouTube comments."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 100,
                    "temperature": 0.7,
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices and "message" in choices[0]:
                        content = choices[0]["message"].get("content")
                        if isinstance(content, str) and content.strip():
                            return content.strip().strip('"')
            except Exception as exc:
                logging.debug(f"OpenRouter REST attempt for {model} failed: {exc}")

        raise RuntimeError("No comment content returned from OpenRouter API.")
