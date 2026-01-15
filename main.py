"""
文字转图片插件
- 自适应高度，手机宽度
- 支持 emoji（Twemoji）
- 支持自动撤回
"""

import asyncio
import base64
import os
from pathlib import Path
from typing import Any, Dict, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star

import astrbot.api.message_components as Comp

from .core import TextRenderer

# 尝试导入 aiocqhttp 事件类型
try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    HAS_AIOCQHTTP = True
except ImportError:
    HAS_AIOCQHTTP = False
    AiocqhttpMessageEvent = None

PLAIN_COMPONENT_TYPES = tuple(
    getattr(Comp, name)
    for name in ("Plain", "Text")
    if hasattr(Comp, name)
)


class Text2ImagePlugin(Star):
    """文字转图片插件"""

    PLUGIN_ID = "astrbot_plugin_text2image"

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self._cfg_obj: AstrBotConfig | dict | None = config
        self._base_dir = Path(__file__).resolve().parent
        self._font_dir = self._base_dir / "ziti"
        self._render_semaphore = asyncio.Semaphore(3)
        self._recall_tasks: list[asyncio.Task] = []
        logger.info("[文字转图片] 插件已加载")

    def cfg(self) -> Dict[str, Any]:
        try:
            return self._cfg_obj if isinstance(self._cfg_obj, dict) else (self._cfg_obj or {})
        except Exception:
            return {}

    def _cfg_bool(self, key: str, default: bool) -> bool:
        val = self.cfg().get(key, default)
        return bool(val) if not isinstance(val, str) else val.lower() in {"1", "true", "yes", "on"}

    async def terminate(self):
        """插件卸载时取消所有撤回任务"""
        for task in self._recall_tasks:
            task.cancel()
        self._recall_tasks.clear()

    def _schedule_recall(self, client, message_id: int):
        """安排撤回消息"""
        recall_time = int(self.cfg().get("recall_time", 0))
        if recall_time <= 0:
            return
        
        async def do_recall():
            try:
                await asyncio.sleep(recall_time)
                await client.delete_msg(message_id=message_id)
                logger.debug(f"[文字转图片] 已撤回消息: {message_id}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"[文字转图片] 撤回消息失败: {e}")
        
        task = asyncio.create_task(do_recall())
        task.add_done_callback(lambda t: self._recall_tasks.remove(t) if t in self._recall_tasks else None)
        self._recall_tasks.append(task)

    async def _render_async(self, text: str) -> Optional[str]:
        try:
            renderer = TextRenderer(self.cfg(), self._font_dir)
            return await asyncio.to_thread(renderer.render, text)
        except Exception as exc:
            logger.error("[文字转图片] 渲染失败: %s", exc)
            return None

    def _chain_to_plain_text(self, chain: list[Any]) -> Optional[str]:
        if not chain:
            return None
        builder: list[str] = []
        for seg in chain:
            if PLAIN_COMPONENT_TYPES and isinstance(seg, PLAIN_COMPONENT_TYPES):
                builder.append(getattr(seg, "text", "") or "")
            elif hasattr(seg, "text") and seg.__class__.__name__.lower() in {"plain", "text"}:
                builder.append(getattr(seg, "text", "") or "")
            else:
                return None
        text = "".join(builder).strip()
        return text if text else None

    @filter.on_decorating_result(priority=-10)
    async def on_decorating_result(self, event: AstrMessageEvent):
        if not self._cfg_bool("enable_render", True):
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        render_scope = str(self.cfg().get("render_scope", "llm_only")).lower()
        resp = event.get_extra("llm_resp")
        is_llm_response = isinstance(resp, LLMResponse)

        if render_scope == "llm_only" and not is_llm_response:
            return

        text = self._chain_to_plain_text(result.chain)
        if not text:
            return

        char_threshold = int(self.cfg().get("render_char_threshold", 0))
        if char_threshold > 0 and len(text) > char_threshold:
            return

        async with self._render_semaphore:
            image_path = await self._render_async(text)

        if not image_path:
            return

        # 检查是否需要自动撤回
        recall_enabled = self._cfg_bool("recall_enabled", False)
        recall_time = int(self.cfg().get("recall_time", 0))
        
        logger.debug(f"[文字转图片] recall_enabled={recall_enabled}, recall_time={recall_time}")
        
        if recall_enabled and recall_time > 0:
            # 检查是否是 aiocqhttp 事件类型
            if HAS_AIOCQHTTP and isinstance(event, AiocqhttpMessageEvent):
                client = event.bot
                logger.debug(f"[文字转图片] 检测到 aiocqhttp 事件, client={client}")
                
                if client is not None:
                    try:
                        group_id = event.get_group_id()
                        user_id = event.get_sender_id()
                        
                        logger.debug(f"[文字转图片] group_id={group_id}, user_id={user_id}")
                        
                        # 读取图片并转为 base64（避免容器间文件路径问题）
                        with open(image_path, 'rb') as f:
                            img_data = base64.b64encode(f.read()).decode('utf-8')
                        
                        # 清理临时文件（读取完成后立即删除）
                        try:
                            os.remove(image_path)
                        except Exception:
                            pass
                        
                        # 构建消息（使用 base64）
                        msg = [{'type': 'image', 'data': {'file': f'base64://{img_data}'}}]
                        
                        # 发送消息
                        if group_id:
                            send_result = await client.send_group_msg(group_id=int(group_id), message=msg)
                        else:
                            send_result = await client.send_private_msg(user_id=int(user_id), message=msg)
                        
                        logger.debug(f"[文字转图片] send_result={send_result}")
                        
                        # 安排撤回
                        if send_result:
                            msg_id = send_result.get('message_id')
                            if msg_id:
                                self._schedule_recall(client, int(msg_id))
                                logger.info(f"[文字转图片] 已安排 {recall_time}s 后撤回消息 {msg_id}")
                        
                        # 清空原消息链，阻止重复发送
                        result.chain.clear()
                        event.stop_event()
                        return
                    except Exception as e:
                        logger.warning(f"[文字转图片] 撤回模式发送失败: {e}，回退普通模式")
                        # 异常时文件可能还未删除，继续走普通模式会处理
            else:
                logger.debug(f"[文字转图片] 非 aiocqhttp 事件类型，使用普通模式")

        # 普通模式：替换消息链（使用 base64 避免临时文件残留）
        try:
            with open(image_path, 'rb') as f:
                img_data = base64.b64encode(f.read()).decode('utf-8')
            result.chain = [Comp.Image(file=f'base64://{img_data}')]
        except Exception as exc:
            logger.error("[文字转图片] 创建图片组件失败: %s", exc)
        finally:
            # 清理临时文件
            try:
                os.remove(image_path)
            except Exception:
                pass

    @filter.on_llm_response(priority=100000)
    async def save_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        event.set_extra("llm_resp", resp)
