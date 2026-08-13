from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseAIProvider(ABC):
    """Abstract Base Class for AI comment generation services."""

    @abstractmethod
    def generate_comment(self, video_context: Dict[str, str]) -> str:
        """
        Generate a realistic, contextual YouTube comment given video title, description, and sample comments.
        
        :param video_context: Dict containing 'title', 'description', 'channel', etc.
        :return: Generated comment string.
        """
        pass
