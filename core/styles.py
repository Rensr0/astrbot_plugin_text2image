"""样式定义"""

from dataclasses import dataclass


@dataclass
class TextSegment:
    """文本片段"""
    text: str
    is_emoji: bool = False
    no_wrap: bool = False
