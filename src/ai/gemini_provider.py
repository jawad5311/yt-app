import logging
from typing import Dict, Any
import requests

from .base import BaseAIProvider


class GeminiAIProvider(BaseAIProvider):
    """Google Gemini AI Provider with model discovery and REST fallback."""

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def generate_comment(self, video_context: Dict[str, str]) -> str:
        if not self.api_key:
            raise ValueError("Gemini API key is empty.")

        title = video_context.get("title", "")
        description = video_context.get("description", "")
        channel = video_context.get("channel", "")

        prompt = (
            "You are a genuine, enthusiastic YouTube viewer leaving a comment on a video.\n"
            f"Video Title: {title}\n"
            f"Channel Name: {channel}\n"
            f"Video Description Snippet: {description[:300]}\n\n"
            "Write a short, engaging, natural 1-2 sentence comment about this video. "
            "Do NOT use generic boilerplate like 'Great video!'. Make it sound like a real person reacting to the topic. "
            "Do NOT include hashtags or emojis excess. Return ONLY the comment text."
        )

        # Try Google GenAI SDK if installed, otherwise REST fallback
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            # Try current flash models
            models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    if response and response.text:
                        return response.text.strip().strip('"')
                except Exception:
                    continue
        except ImportError:
            pass

        # Fallback to direct v1beta REST endpoint if SDK is unavailable or failed
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and "text" in parts[0]:
                return parts[0]["text"].strip().strip('"')

        raise RuntimeError("No comment returned from Gemini REST API.")
