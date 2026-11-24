"""Typed, transport-neutral Base operations. No MCP server/session is used."""
from dataclasses import dataclass
from inspect import getdoc, isawaitable, signature
from pathlib import Path
from typing import Callable, get_type_hints

from pydantic import BaseModel, create_model

@dataclass(frozen=True)
class Operation:
    handler: Callable
    arguments: type[BaseModel]
    description: str

class OperationRegistry:
    def __init__(self):
        self._operations = {}

    def tool(self):
        """Accept existing canonical typed handler declarations, without an MCP adapter."""
        def register(fn):
            fields = {}
            types = get_type_hints(fn, include_extras=True)
            for name, param in signature(fn).parameters.items():
                if name not in types or param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
                    raise ValueError("Operation must declare typed named arguments")
                fields[name] = (types[name], ... if param.default is param.empty else param.default)
            if fn.__name__ in self._operations:
                raise ValueError("Duplicate operation")
            model = create_model(fn.__name__ + "Arguments", **fields)
            self._operations[fn.__name__] = Operation(fn, model, getdoc(fn) or "")
            return fn
        return register

    def descriptors(self):
        return {name: {"inputSchema": operation.arguments.model_json_schema(),
                       "description": operation.description}
                for name, operation in sorted(self._operations.items())}

    async def invoke(self, name, args):
        operation = self._operations[name]
        model = operation.arguments
        if type(args) is not dict or not args.keys() <= model.model_fields.keys():
            raise ValueError("Unknown arguments")
        parsed = model.model_validate(args, strict=True)
        value = operation.handler(**{name: getattr(parsed, name) for name in model.model_fields})
        return await value if isawaitable(value) else value

def create_operations(config, store, *, background=False, service=None):
    # Transitional imports of shared declarations: this never creates FastMCP or a listener.
    # The legacy binary-artifact handler is not authorized on the hosted HTTP policy.
    from .mcp_server import WorkerLauncher, _register_read_tools, _register_write_tools
    if Path(store.path).resolve() != Path(config.db).resolve():
        raise ValueError("Caller-owned store must match configured database")
    if service is not None and service.store is not store:
        raise ValueError('Injected operations service must use the caller-owned store')
    service = config.service(store) if service is None else service
    launcher = WorkerLauncher(config)
    registry = OperationRegistry()
    _register_write_tools(registry, service, launcher, background)
    _register_read_tools(registry, service, config, launcher if background else None)
    return registry
