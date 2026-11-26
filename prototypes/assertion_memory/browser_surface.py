"""Browser carrier excludes static and all private/nonbrowser path families."""
from starlette.responses import JSONResponse

def browser_surface(graph):
    if not callable(graph):
        raise ValueError('browser Graph ASGI application required')
    async def serve(scope, receive, send):
        if scope['type'] == 'lifespan':
            return await graph(scope, receive, send)
        if scope['type'] != 'http':
            return
        path=scope.get('path','')
        raw=scope.get('raw_path',b'')
        if not path.startswith('/api/v1/') or not raw.startswith(b'/api/v1/'):
            return await JSONResponse({'error':'not_found'},status_code=404,
                headers={'Cache-Control':'no-store'})(scope,receive,send)
        return await graph(scope, receive, send)
    return serve
