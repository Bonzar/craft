#!/usr/bin/env python3
"""Codex Cloud review client. Python 3 standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a service credential to a login page or another origin.
        return None

def request(base, token, method, path, payload=None):
    headers = {'Content-Type': 'application/json', 'X-Review-Client': 'codex'}
    if token:
        headers['OAI-Sites-Authorization'] = 'Bearer ' + token
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(base + '/api/' + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as response:
            if 'application/json' not in response.headers.get('Content-Type', ''):
                raise RuntimeError('Expected JSON. Check private Site access and REVIEW_TOKEN.')
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (301, 302, 303, 307, 308, 401, 403):
            raise RuntimeError('Access denied. Configure the Site service credential as REVIEW_TOKEN for this exact host.') from None
        try:
            detail = json.load(error).get('error', 'request_failed')
        except (ValueError, AttributeError):
            detail = 'request_failed'
        raise RuntimeError('HTTP %s: %s' % (error.code, detail)) from None
    except urllib.error.URLError:
        raise RuntimeError('Connection failed. Check the allowed domain and GET/POST network methods.') from None

def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('REVIEW_URL'), help='HTTPS Site origin; defaults to REVIEW_URL')
    parser.add_argument('--session', default=os.environ.get('REVIEW_SESSION'), help='Explicit session ID for this chat')
    parser.add_argument('--token-stdin', action='store_true', help='Read credential from stdin instead of REVIEW_TOKEN')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    create = commands.add_parser('session-create'); create.add_argument('--title', required=True); create.add_argument('--id')
    select = commands.add_parser('select'); select.add_argument('review')
    html = commands.add_parser('publish-html'); html.add_argument('file', type=Path); html.add_argument('--id', required=True); html.add_argument('--title', required=True); html.add_argument('--no-select', action='store_true')
    publish = commands.add_parser('publish'); publish.add_argument('file', type=Path)
    read = commands.add_parser('read'); read.add_argument('review', nargs='?'); read.add_argument('--open', action='store_true')
    wait = commands.add_parser('wait'); wait.add_argument('review', nargs='?'); wait.add_argument('--cursor'); wait.add_argument('--timeout', type=int, default=50)
    reply = commands.add_parser('reply'); reply.add_argument('thread'); reply.add_argument('--body-file', type=Path, required=True); reply.add_argument('--request-id')
    comment = commands.add_parser('comment'); comment.add_argument('file', type=Path)
    resolve = commands.add_parser('resolve'); resolve.add_argument('thread'); resolve.add_argument('--version', required=True, type=int); resolve.add_argument('--reopen', action='store_true')
    args = parser.parse_args()
    if not args.url:
        parser.error('Set REVIEW_URL or --url.')
    url = urllib.parse.urlsplit(args.url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/') or url.port not in (None, 443):
        parser.error('Use an HTTPS origin without credentials, query, path, or nonstandard port.')
    base = args.url.rstrip('/')
    token = sys.stdin.readline().strip() if args.token_stdin else os.environ.get('REVIEW_TOKEN', '')
    if args.command != 'session-create' and not args.session:
        parser.error('Pass --session for this chat. Create one with session-create first.')
    scope_path = 'sessions/' + urllib.parse.quote(args.session or '', safe='')
    if args.command == 'session-create':
        payload = {'title': args.title}
        if args.id:
            payload['id'] = args.id
        result = request(base, token, 'POST', 'sessions', payload)
        result['url'] = base + '/?session=' + urllib.parse.quote(result['id'], safe='')
    elif args.command == 'list':
        result = request(base, token, 'GET', scope_path)
    elif args.command == 'select':
        result = request(base, token, 'POST', 'select', {'session': args.session, 'review': args.review})
    elif args.command == 'publish-html':
        result = request(base, token, 'POST', 'reviews', {'session': args.session, 'id': args.id, 'title': args.title, 'html': args.file.read_text(), 'activate': not args.no_select})
        result['url'] = base + '/?session=' + urllib.parse.quote(args.session, safe='')
    elif args.command == 'publish':
        payload = json.loads(args.file.read_text()); payload['session'] = args.session
        result = request(base, token, 'POST', 'reviews', payload)
        result['url'] = base + '/?session=' + urllib.parse.quote(args.session, safe='')
    elif args.command in ('read', 'wait'):
        def snapshot():
            selected = args.review or request(base, token, 'GET', scope_path)['session']['activeReview']
            if not selected:
                raise RuntimeError('Session has no selected document. Publish HTML first.')
            return request(base, token, 'GET', scope_path + '/reviews/' + urllib.parse.quote(selected, safe=''))
        result = snapshot()
        if args.command == 'wait':
            if not 0 <= args.timeout <= 60:
                parser.error('--timeout must be between 0 and 60 seconds.')
            initial = args.cursor or fingerprint(result)
            deadline = time.monotonic() + args.timeout
            while fingerprint(result) == initial and time.monotonic() < deadline:
                time.sleep(min(3, max(0, deadline-time.monotonic())))
                result = snapshot()
            result['changed'] = fingerprint(result) != initial
        cursor = fingerprint({k:v for k,v in result.items() if k != 'changed'})
        if args.command == 'read' and args.open:
            result['threads'] = [t for t in result['threads'] if not t['resolved']]
        result['cursor'] = cursor
    elif args.command == 'reply':
        result = request(base, token, 'POST', 'replies', {'session': args.session, 'thread': args.thread, 'body': args.body_file.read_text(), 'requestId': args.request_id or str(uuid.uuid4())})
    elif args.command == 'comment':
        payload = json.loads(args.file.read_text()); payload['session'] = args.session; payload.setdefault('requestId', str(uuid.uuid4()))
        result = request(base, token, 'POST', 'comments', payload)
    else:
        result = request(base, token, 'POST', 'resolve', {'session': args.session, 'thread': args.thread, 'resolved': not args.reopen, 'expectedVersion': args.version})
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
