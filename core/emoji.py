"""Emoji 处理器"""

import re
from io import BytesIO
from typing import Dict, List, Optional
from urllib.request import urlopen

from PIL import Image

from .styles import TextSegment


class EmojiHandler:
    """Emoji 处理器"""
    
    PATTERN = re.compile(
        "(["
        "\U0001F600-\U0001F64F"  # 表情
        "\U0001F300-\U0001F5FF"  # 符号
        "\U0001F680-\U0001F6FF"  # 交通
        "\U0001F1E0-\U0001F1FF"  # 旗帜
        "\U0001F900-\U0001F9FF"  # 补充符号
        "\U0001FA00-\U0001FA6F"  # 象棋
        "\U0001FA70-\U0001FAFF"  # 扩展
        "\U0001F000-\U0001F0FF"  # 麻将等
        "\U00002600-\U000027BF"  # 杂项符号（包含 ⭐ 2B50 之前的）
        "\U00002B50-\U00002B55"  # 星星等符号 ⭐⭕
        "\U00002300-\U000023FF"  # 技术符号
        "\U00002702-\U000027B0"  # 装饰符号
        "\U0000FE00-\U0000FE0F"  # 变体选择符
        "\U0000200D"             # 零宽连接符
        "])+",
        re.UNICODE
    )
    
    SEPARATOR_CHARS = '━─═—_-~·•'
    
    def __init__(self):
        self._cache: Dict[str, Image.Image] = {}
    
    def split_text(self, text: str) -> List[TextSegment]:
        """将文本拆分为普通文字和 emoji"""
        result = []
        for part in self.PATTERN.split(text):
            if not part:
                continue
            if self.PATTERN.fullmatch(part):
                result.append(TextSegment(text=part, is_emoji=True))
            else:
                result.extend(self._split_separators(part))
        return result
    
    def _split_separators(self, text: str) -> List[TextSegment]:
        """拆分连续分隔符"""
        result = []
        i = 0
        while i < len(text):
            char = text[i]
            j = i + 1
            while j < len(text) and text[j] == char:
                j += 1
            
            if j - i >= 3 and char in self.SEPARATOR_CHARS:
                result.append(TextSegment(text=text[i:j], no_wrap=True))
            else:
                result.append(TextSegment(text=text[i:j]))
            i = j
        return result
    
    def get_image(self, emoji: str, size: int) -> Optional[Image.Image]:
        """获取 emoji 图片"""
        cache_key = f"{emoji}_{size}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()
        
        for url in self._get_twemoji_urls(emoji):
            try:
                with urlopen(url, timeout=5) as response:
                    img = Image.open(BytesIO(response.read())).convert("RGBA")
                    img = img.resize((size, size), Image.LANCZOS)
                    self._cache[cache_key] = img
                    return img.copy()
            except Exception:
                continue
        return None
    
    def _get_twemoji_urls(self, emoji: str) -> list:
        """生成多种可能的 Twemoji URL"""
        urls = []
        base = "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72"
        
        # 格式1: 移除变体选择符
        cleaned = emoji.replace('\ufe0f', '')
        codepoints = '-'.join(f'{ord(c):x}' for c in cleaned)
        urls.append(f"{base}/{codepoints}.png")
        
        # 格式2: 保留变体选择符
        codepoints_with_fe0f = '-'.join(f'{ord(c):x}' for c in emoji)
        if codepoints_with_fe0f != codepoints:
            urls.append(f"{base}/{codepoints_with_fe0f}.png")
        
        # 格式3: 只取第一个字符
        if len(cleaned) > 0:
            single = f'{ord(cleaned[0]):x}'
            if single != codepoints:
                urls.append(f"{base}/{single}.png")
        
        return urls
