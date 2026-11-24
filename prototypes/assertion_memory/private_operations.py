"""Host lifecycle for the plain private operation API; no MCP transport."""
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from .mcp_server import WorkerLauncher
from .operation_registry import create_operations
from .storage import MemoryStore

def create_private_operations_app(config, mount_operations, *, background=True):
    """One canonical storage connection and existing worker-launcher policy."""
    @asynccontextmanager
    async def lifespan(_app):
        try:
            if background:
                WorkerLauncher(config).start()
            yield
        finally:
            store.close()

    store = MemoryStore(config.db)
    try:
        operations = create_operations(config, store, background=background)
        inner = mount_operations(operations)
        app = Starlette(routes=[Mount("/", app=inner)], lifespan=lifespan)
        app.router.redirect_slashes = False
        return app
    except BaseException:
        store.close()
        raise
