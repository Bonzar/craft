"""Offline exact guard and hook transform requiring approved embedded adapters."""
import ast
from hashlib import sha256
from transform_settled_guard import transform_pinned_guard
from inspect_ops_source_pair import HOOK

def _name_set(tree):
    return {f.name for f in tree.body if type(f) is ast.FunctionDef}

def _embedded(source, pin, definitions):
    if type(source) is not bytes or sha256(source).hexdigest()!=pin or not source.endswith(b'\n'):
        raise ValueError('unreviewed embedded adapter')
    tree=ast.parse(source)
    if not set(definitions)<=_name_set(tree):
        raise ValueError('missing exact embedded adapter entrypoints')
    return source

def _find_function(tree,name):
    found=[f for f in tree.body if type(f) is ast.FunctionDef and f.name==name]
    if len(found)!=1: raise ValueError('changed protected source shape')
    return found[0]

def _append_before_main(source, helper):
    tree=ast.parse(source)
    matches=[]
    for node in tree.body:
        if isinstance(node,ast.If) and ast.dump(node.test)==ast.dump(
                ast.parse("__name__ == '__main__'",mode='eval').body):
            matches.append(node)
    if len(matches)!=1:
        raise ValueError('changed protected entrypoint order')
    lines=source.splitlines(keepends=True)
    at=matches[0].lineno-1
    result=b''.join(lines[:at])+b'\n'+helper+b'\n'+b''.join(lines[at:])
    compile(result,'protected Base candidate','exec',dont_inherit=True)
    return result

def _transform_hook(raw,expected):
    if type(raw) is not bytes or sha256(raw).hexdigest()!=expected:
        raise ValueError('unverified original PROD service hook')
    tree=ast.parse(raw)
    check=_find_function(tree,'check_live')
    classes=[x for x in tree.body if type(x) is ast.ClassDef and x.name=='Smoke']
    if len(classes)!=1:
        raise ValueError('unexpected original Smoke class')
    smoke=_find_function(classes[0],'run')
    mcp=[n for n in smoke.body if n.end_lineno==165]
    browser=[n for n in smoke.body if n.lineno==166]
    if (check.lineno!=309 or smoke.lineno!=155 or len(mcp)!=1 or len(browser)!=1
            or smoke.body.index(browser[0])!=smoke.body.index(mcp[0])+1
            or len(check.body)<6 or check.body[0].lineno!=310
            or check.body[4].lineno!=315 or type(check.body[5]) is not ast.For
            or check.body[5].lineno!=316 or check.body[5].end_lineno!=329):
        raise ValueError('unexpected exact old application proof boundary')
    old_loop=check.body[5]
    if (len(old_loop.body)<6
            or [(n.lineno,n.end_lineno) for n in old_loop.body[:3]]!=[(317,317),(318,318),(319,319)]
            or any(type(n) is not t for n,t in zip(old_loop.body[:3],(ast.Assign,ast.Assign,ast.Expr)))):
        raise ValueError('wrong inherited fixed systemd metadata checks')
    original=raw.splitlines(keepends=True)
    # The callback is nested in original check_live, so all inherited common
    # config and scope locals remain authoritative. Reuse the native exact
    # metadata read, split, and trusted fragment validation byte-for-byte.
    native_pid=(b'        def _base_native_pid(unit):\n'
                b'            i = UNITS.index(unit)\n'
                + b''.join(b'    '+line for line in original[316:319])
                + b'            found = values.get("MainPID", "")\n'
                  b'            if not re.fullmatch(r"0|[1-9][0-9]{0,9}", found):\n'
                  b'                raise ValueError("invalid trusted MainPID")\n'
                  b'            return int(found)\n')
    # Preserve native private MCP probes exactly. Existing owner UI handling
    # is separate from runtime witness; do not create a browser session.
    additions={
        166:b'        if _base_browser_sidecar_enabled():\n            return\n',
        316:
             b'    if _base_browser_sidecar_enabled():\n'
             + native_pid +
             b'        return _base_browser_sidecar_check_live(policy, release, uid,\n'
             b'              trusted_pid=_base_native_pid, not_listening=StartupNotListening)\n',
    }
    lines=[]
    for number,line in enumerate(raw.splitlines(keepends=True),1):
        lines.append(additions.get(number,b''))
        lines.append(line)
    return b''.join(lines)

def compose(*, raw_guard, raw_hook, guard_adapter, guard_adapter_pin, hook_adapter, hook_adapter_pin):
    guard_helpers=_embedded(guard_adapter,guard_adapter_pin,('_base_browser_restart_selected',))
    hook_helpers=_embedded(hook_adapter,hook_adapter_pin,('_base_browser_sidecar_enabled',
                                                         '_base_browser_sidecar_check_live'))
    guard=_append_before_main(transform_pinned_guard(raw_guard),guard_helpers)
    hook=_append_before_main(_transform_hook(raw_hook,HOOK[1]),hook_helpers)
    return guard,hook
