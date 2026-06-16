from .chat import register_chat_tools
from .knowledge import register_knowledge_tools
from .memory import register_memory_tools
from .saving import register_saving_tools
from .vision import register_vision_tools
from .web import register_web_tools


def register_native_tools(registry) -> None:
    register_chat_tools(registry)
    register_knowledge_tools(registry)
    register_memory_tools(registry)
    register_web_tools(registry)
    register_vision_tools(registry)
    register_saving_tools(registry)
