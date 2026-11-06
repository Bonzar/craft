"""Инструменты Craft MCP: имя инструмента и команда из его входа.

Имя сервера Craft MCP зависит от того, как его подключили, поэтому
инструмент узнаётся по хвосту имени: `mcp__‹сервер›__craft_write`.
"""

MCP_PREFIX = 'mcp__'
MCP_NAME_SEPARATOR = '__'
# Хвост имени любого инструмента Craft MCP.
CRAFT_TOOL_PREFIX = 'craft_'
# write_and_show исполняет те же команды, что write: обе — запись.
CRAFT_WRITE_TOOLS = ('craft_write', 'craft_write_and_show')
CRAFT_READ_TOOL = 'craft_read'
COMMAND_FIELD = 'command'


def craft_tool(tool_name) -> str:
    """Хвост имени MCP-инструмента; не MCP-инструмент — пустая строка.

    Хвост отдаётся у любого MCP-сервера: Craft ли это, решает тот, кто
    зовёт, по CRAFT_TOOL_PREFIX или по именам ниже.
    """
    if not isinstance(tool_name, str) or not tool_name.startswith(MCP_PREFIX):
        return ''
    tail = tool_name.rsplit(MCP_NAME_SEPARATOR, 1)[-1]
    # Apps добавляет slug плагина Craft к имени его capability.
    app_names = {f'craft_{name}': name for name in (*CRAFT_WRITE_TOOLS, CRAFT_READ_TOOL)}
    return app_names.get(tail, tail)


def is_craft_write(tool_name) -> bool:
    """Пишет ли инструмент в Craft: craft_write или craft_write_and_show."""
    return craft_tool(tool_name) in CRAFT_WRITE_TOOLS


def is_craft_read(tool_name) -> bool:
    """Читает ли инструмент Craft: craft_read."""
    return craft_tool(tool_name) == CRAFT_READ_TOOL


def command_of(tool_input) -> str:
    """Строка команды из входа инструмента; нет строки — пустая строка."""
    command = tool_input.get(COMMAND_FIELD) if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ''
