"""Pure TEST release-stage activation proof. No environment, file or network access."""
import re

TEST_UNIT = 'assertion-test-deploy-graph.service'
TEST_ROOT = '/srv/assertion-release/test'
TEST_ORIGIN = 'https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
OWNER_STREAM_FLAG = '--owner-snapshot-events'
SHA = re.compile(r'[0-9a-f]{64}\Z')
BASE_TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+\Z')
VISUAL_TAG = re.compile(r'assertion-memory-visual-v[0-9]+\.[0-9]+\.[0-9]+\Z')
ARGV = re.compile(r'[A-Za-z0-9._:/-]+\Z')
PINNED_VALUES = {
    '--source': TEST_ROOT + '/state/fictional.sqlite3',
    '--state-dir': TEST_ROOT + '/state/graph-state',
    '--password-file': TEST_ROOT + '/state/graph-password',
    '--static-dir': TEST_ROOT + '/current/apps/graph_viewer/web/dist',
    '--public-origin': TEST_ORIGIN, '--port': '8891',
}
FLAGS = {'--browser-owner-api', '--synthetic-fixture'}
VALUE_FLAGS = set(PINNED_VALUES) | {'--host', '--test-direct-events-origin',
    '--queue-status-config', '--queue-status-stage', '--provider-id',
    '--embedding-model', '--embedding-cache', '--queue-read-token-file'}
PREFIX = [TEST_ROOT + '/current/venv/bin/python', '-B', '-m', 'apps.graph_viewer.server']


def tags(base, visual):
    return (type(base) is str and BASE_TAG.fullmatch(base)
            and type(visual) is str and VISUAL_TAG.fullmatch(visual))


def identity(lane, unit, listener):
    if (lane, unit, listener) != ('test', TEST_UNIT, '127.0.0.1:8891'):
        raise ValueError('wrong TEST Graph release target')


def plan(flag=False, *, lane, unit, listener, argv, expected_base, expected_visual,
         effective_base, effective_visual, expected_base_sha, expected_visual_sha,
         effective_base_sha, effective_visual_sha):
    """Return argv only, for a separately trusted, exact-match protected executor."""
    identity(lane, unit, listener)
    if type(flag) is not bool:
        raise ValueError('owner snapshot flag must be boolean')
    if not tags(expected_base, expected_visual) or (effective_base, effective_visual) != (expected_base, expected_visual):
        raise ValueError('published release pair mismatch')
    for expected, actual in ((expected_base_sha, effective_base_sha), (expected_visual_sha, effective_visual_sha)):
        if type(expected) is not str or not SHA.fullmatch(expected) or expected != actual:
            raise ValueError('published archive digest mismatch')
    if (type(argv) is not list or not 4 < len(argv) <= 42 or argv[:4] != PREFIX
            or any(type(x) is not str or not 0 < len(x) <= 330 or not ARGV.fullmatch(x)
                   or any(part in {'.', '..'} for part in x.split('/')) for x in argv)):
        raise ValueError('wrong effective TEST Graph command')
    values = {}
    i = 4
    while i < len(argv):
        key = argv[i]
        i += 1
        if key in values or key not in FLAGS | VALUE_FLAGS:
            raise ValueError('unexpected or repeated TEST Graph option')
        if key in FLAGS:
            values[key] = True
        elif i == len(argv) or argv[i].startswith('--'):
            raise ValueError('missing TEST Graph option value')
        else:
            values[key] = argv[i]
            i += 1
    if (any(values.get(k) != v for k, v in PINNED_VALUES.items()) or
            values.get('--host', '127.0.0.1') != '127.0.0.1' or
            any(values.get(k) is not True for k in FLAGS)):
        raise ValueError('wrong TEST Graph owner, fixture, origin or bind')
    q = (values.get('--queue-status-config'), values.get('--queue-status-stage'))
    if q != ('/run/assertion-memory-dashboard/test/scheduler.json', 'test'):
        raise ValueError('TEST queue snapshot projection required')
    return [*argv, OWNER_STREAM_FLAG] if flag else list(argv)


def prove(*, response, expected_base, expected_visual):
    """Check metadata-only measurements from one owner navigation, not a buffered pair."""
    needed = {'base', 'visual', 'anonymous_denied', 'same_origin', 'no_store',
              'unbuffered', 'bootstrap', 'queue_hints', 'epoch_matched',
              'single_connection', 'connecting_ms', 'request_ms', 'hello_reset_ms',
              'live_ms', 'graph_dispatch_ms', 'queue_dispatch_ms', 'heartbeat_ms',
              'later_change_ms', 'disconnect_downgraded'}
    if type(response) is not dict or set(response) != needed or not tags(expected_base, expected_visual):
        raise ValueError('invalid TEST owner stream evidence')
    if (response['base'], response['visual']) != (expected_base, expected_visual):
        raise ValueError('wrong ordinary published TEST release')
    if any(response[k] is not True for k in needed - {'base', 'visual'} if not k.endswith('_ms')):
        raise ValueError('TEST owner stream contract incomplete')
    for name in needed:
        if name.endswith('_ms') and (type(response[name]) is not int or not 0 <= response[name] <= 120000):
            raise ValueError('invalid TEST measurement')
    r = response
    if not (r['connecting_ms'] <= r['request_ms'] <= r['hello_reset_ms'] <= r['live_ms']
            <= min(r['graph_dispatch_ms'], r['queue_dispatch_ms'])
            and r['hello_reset_ms'] < r['heartbeat_ms'] < r['later_change_ms']):
        raise ValueError('TEST owner stream ordering mismatch')
    return True
