"""Compact source-equivalent evidence disposition, with effective-time gating."""
from collections import defaultdict
import json
from .common import timestamp


def components(adjacency):
    """Iterative Kosaraju avoids recursion and per-edge reachability searches."""
    reverse = defaultdict(list)
    nodes = set(adjacency)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].append(source)
            nodes.add(target)
    visited, ordered = set(), []
    for start in nodes:
        if start in visited:
            continue
        stack = [(start,False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                ordered.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node,True))
                stack.extend((child,False) for child in adjacency.get(node,()) if child not in visited)
    labels = {}
    for start in reversed(ordered):
        if start in labels:
            continue
        stack = [start]
        while stack:
            node = stack.pop()
            if node not in labels:
                labels[node] = start
                stack.extend(reverse.get(node,()))
    return labels


def classify_link(row, states, status_at):
    source, target = states[row['f']], states[row['t']]
    sk, tk = source['kind'], target['kind']
    if 'proposal' in (sk,tk) or 'inference' in (sk,tk):
        return None
    kind, meta = row['kind'], json.loads(row['metadata'])
    if kind=='supersedes':
        dates = [v for v in (source['valid_at'],meta.get('effective_at')) if v]
        if dates and max(timestamp(v) for v in dates)>status_at:
            return None
        if meta.get('condition') or source['scope']!=target['scope']:
            return None
        return 'replacement' if tk is None or sk==tk else 'conflict'
    return {'contradicts':'conflict','exception_to':'exception'}.get(kind)


def apply_status(c, states, status_at, subset=None):
    graph, conflicts, exceptions = defaultdict(list), set(), set()
    if subset is None:
        c.execute('DELETE FROM status_links')
        rows=c.execute("SELECT ordinal,id,f,t,kind,metadata FROM edges WHERE kind IN ('supersedes','contradicts','exception_to')")
    else:
        c.executemany('DELETE FROM status_links WHERE f=?',((n,) for n in subset))
        rows=local_edges(c,subset)
    for row in rows:
        meaning = classify_link(row, states, status_at)
        if meaning=='replacement':
            graph[row['f']].append(row['t'])
            c.execute('INSERT INTO status_links VALUES(?,?,?,?)',(row['id'],row['f'],row['t'],meaning))
        elif meaning=='conflict':
            conflicts.update((row['f'],row['t']))
        elif meaning=='exception':
            exceptions.add(row['f'])
    labels, superseded = components(graph), set()
    for source, targets in graph.items():
        for target in targets:
            if labels[source]==labels[target]:
                conflicts.update((source,target))
                c.execute('DELETE FROM status_links WHERE f=? AND t=?',(source,target))
            else:
                superseded.add(target)
    updates=[]
    for ordinal, state in states.items():
        status = 'superseded' if ordinal in superseded else 'conflict' if ordinal in conflicts else 'exception' if ordinal in exceptions else state['kind'] or 'assertion'
        cancelled = state.get('cancelled',False)
        if cancelled:
            status='cancelled'
        flags=(1 if status=='superseded' else 0)|(2 if cancelled else 0)
        updates.append((flags,status,int(ordinal in conflicts),ordinal))
    c.executemany('UPDATE nodes SET flags=?,disposition=?,conflict=? WHERE ordinal=?',updates)


def local_edges(c,nodes):
    for ordinal in nodes:
        yield from c.execute("SELECT ordinal,id,f,t,kind,metadata FROM edges WHERE f=? AND kind IN ('supersedes','contradicts','exception_to')",(ordinal,))


def effective_boundaries(c,after,until=None):
    result=[]
    for row in c.execute("SELECT e.f,e.t,e.metadata,n.valid_at FROM edges e JOIN nodes n ON n.ordinal=e.f WHERE e.kind='supersedes'"):
        meta=json.loads(row['metadata'])
        dates=[timestamp(value) for value in [row['valid_at'],meta.get('effective_at')] if value]
        if not dates:
            continue
        at=max(dates)
        if at>after and (until is None or at<=until):
            result.append((at,row['f'],row['t']))
    return result


def next_effective_at(c,after):
    return min((row[0] for row in effective_boundaries(c,after)),default=None)
