"""Read-only TEST ingress contract. No networking, credentials or effects here."""
from hashlib import sha256
import json
import re

URL = ('https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
       '/api/v1/capabilities?queue_status=1&limit=1&offset=0')
TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?')
HEX40 = re.compile(r'[a-f0-9]{40}')
HEX64 = re.compile(r'[a-f0-9]{64}')
LIMIT = 64 * 1024


def identity(tag, revision, archive):
    if not (type(tag) is type(revision) is type(archive) is str and TAG.fullmatch(tag)
            and HEX40.fullmatch(revision) and HEX64.fullmatch(archive)):
        raise ValueError('invalid_expected_test_release')
    return sha256(json.dumps(dict(tag=tag, revision=revision, archive=archive, lane='test'),
                             sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def unique_object(items):
    result = dict(items)
    if len(result) != len(items):
        raise ValueError('duplicate_test_ingress_field')
    return result


def read_identity(read, tag, revision, archive):
    """read(url) uses pre-existing owner auth, verifying TLS/no redirect and bounding bytes."""
    generation = identity(tag, revision, archive)
    url, status, media_type, body = read(URL)
    if (url != URL or type(status) is not int or status != 200 or media_type != 'application/json'
            or type(body) is not bytes or len(body) > LIMIT):
        raise ValueError('unexpected_test_ingress_response')
    value = json.loads(body, object_pairs_hook=unique_object)
    if (type(value) is not dict or value.get('stage') != 'test' or value.get('release_version') != tag
            or value.get('release_generation') != generation):
        raise ValueError('stale_or_foreign_test_ingress')
    return generation


def activation(receipt, expected, read):
    """Call only in same-run protected context which already verified receipt provenance."""
    tag, revision, archive = expected
    identity(tag, revision, archive)
    if receipt != {'status':'ok', 'environment':'test', 'revision':revision, 'sha256':archive}:
        raise ValueError('unbound_test_activation_receipt')
    return read_identity(read, tag, revision, archive)


def restored_view(candidate, predecessor, read):
    """Observation only: old public generation is necessary, never proof restore occurred."""
    candidate_generation = identity(*candidate)
    predecessor_generation = identity(*predecessor)
    if candidate_generation == predecessor_generation:
        raise ValueError('unchanged_test_restore_target')
    return read_identity(read, *predecessor)
