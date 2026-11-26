"""Proof-targeted offline AST transform; never executes or writes host files."""
import ast
from hashlib import sha256

LIVE_GUARD_SHA='1b09e5d77c338a148a9df471e0ed010c82f104a2f310a0e45b73cd31f67c6c34'
SETTLED='receive_restart_after_settlement'
# Body supplied by the CONTROL deployment, never the candidate release.
# This source tool only switches the exact settled restart call to that
# separately reviewed fixed package classifier; it does not add privilege.
ADAPTER='_base_browser_restart_selected'

_WANT=ast.dump(ast.parse("run([*userctl(False), 'restart', *NEW], timeout=18)",mode='eval').body)

def _transform(source, expected):
    if type(source) is not bytes or sha256(source).hexdigest()!=expected:
        raise ValueError('unexpected exact old settled-guard digest')
    old=source.decode('utf-8')
    module=ast.parse(old)
    funcs=[node for node in module.body if type(node) is ast.FunctionDef and node.name==SETTLED]
    if len(funcs)!=1:
        raise ValueError('unexpected settled function')
    target=funcs[0]
    calls=[call for call in ast.walk(target)
           if type(call) is ast.Call and ast.dump(call)==_WANT]
    if len(calls)!=1:
        raise ValueError('unexpected fixed settled restart call')
    call=calls[0]
    parents={child:node for node in ast.walk(target) for child in ast.iter_child_nodes(node)}
    expression=parents.get(call)
    manager=parents.get(expression)
    if (type(expression) is not ast.Expr or type(manager) is not ast.With
            or expression not in manager.body):
        raise ValueError('restart call not directly inside the verified fence')
    fence=ast.dump(ast.parse('fence_lock(False)',mode='eval').body)
    if len(manager.items)!=1 or ast.dump(manager.items[0].context_expr)!=fence:
        raise ValueError('unexpected settled guard fence')
    def verifies(nodes):
        wanted=ast.dump(ast.parse('verify_settled()',mode='eval').body)
        return any(type(stmt) is ast.Expr and ast.dump(stmt.value)==wanted for stmt in nodes)
    pos=manager.body.index(expression)
    if not verifies(manager.body[:pos]) or not verifies(manager.body[pos+1:]):
        raise ValueError('restart missing before/after settlement verification')
    # Replace only the exact source interval. Preserve first-start, dispatch,
    # original guards and surrounding source bytes untouched.
    lines=source.splitlines(keepends=True)
    if call.lineno!=call.end_lineno:
        raise ValueError('unexpected multi-line settled call')
    row=call.lineno-1
    lines[row]=lines[row][:call.col_offset]+(ADAPTER+'()').encode()+lines[row][call.end_col_offset:]
    candidate=b''.join(lines)
    ast.parse(candidate)
    return candidate

def transform_pinned_guard(exact_read_only_source):
    """Use only the known production script digest; caller owns exact source."""
    return _transform(exact_read_only_source,LIVE_GUARD_SHA)
