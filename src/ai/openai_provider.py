import logging
from typing import Dict, Any
from .base import BaseAIProvider


class OpenAIProvider(BaseAIProvider):
    """OpenAI ChatGPT Provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def generate_comment(self, video_context: Dict[str, str]) -> str:
        if not self.api_key:
            raise ValueError("OpenAI API key is empty.")

        import openai
        client = openai.OpenAI(api_key=self.api_key)

        title = video_context.get("title", "")
        description = video_context.get("description", "")
        channel = video_context.get("channel", "")

        prompt = (
            "You are a genuine YouTube user leaving a short, friendly comment on a video.\n"
            f"Video Title: {title}\n"
            f"Channel: {channel}\n"
            f"Description: {description[:300]}\n\n"
            "Write a natural 1-2 sentence comment related to this video content. "
            "Return ONLY the comment text without quotes."
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You write engaging, authentic YouTube comments."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=100,
            temperature=0.7,
        )

        content = response.choices[0].message.content
        if content:
            return content.strip().strip('"')
        raise RuntimeError("No comment returned from OpenAI API.")
