"""Authenticate to the existing Sites visual entry, independently of gateway smoke.

No credentials or network calls by default. Integrators supply an already
approved Sites transport for this stage. The gateway and SSH credentials are
not Sites credentials. A passing result attests byte identity, not rendering.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import re
import tarfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ORIGINS = {
    "test": "https://assertion-memory-preview.lush-lemur-7946.chatgpt.site",
    "prod": "https://assertion-memory-cloud-connector.lush-lemur-7946.chatgpt.site",
}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ASSET_NAMES = tuple(re.compile(pattern) for pattern in (
    r"assets/index-[A-Za-z0-9_-]+\.js",
    r"assets/index-[A-Za-z0-9_-]+\.css",
    r"assets/graph\.worker-[A-Za-z0-9_-]+\.js",
))
MAX_BODY = 2_400_000
TIMEOUT = 15
AUTH_HEADERS = frozenset({"cookie", "oai-sites-authorization"})


class SmokeError(ValueError):
    """Deliberately omits response bodies, credentials and transport details."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def scoped_sites_get(lane, approved_auth_headers):
    """Build a no-redirect GET limited to the exact existing stage origin.

    Supply a supported Sites transport only once that service identity and
    tenant have been approved and tested. Do not fabricate identity headers.
    """
    origin = ORIGINS.get(lane)
    if origin is None or not approved_auth_headers:
        raise SmokeError("Sites transport unavailable")
    headers = {}
    for name, value in approved_auth_headers.items():
        key = name.lower()
        if (key not in AUTH_HEADERS or key in headers or not isinstance(value, str)
                or not value or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise SmokeError("Unsupported Sites transport credential")
        headers[key] = value
    opener = build_opener(ProxyHandler({}), NoRedirect())
    allowed_path = re.compile(
        r"/visual/[0-9a-f]{64}/(?:assets/(?:index-[A-Za-z0-9_-]+\.(?:css|js)|graph\.worker-[A-Za-z0-9_-]+\.js))?\Z"
    )

    @contextmanager
    def get(url, *, timeout):
        path = url.removeprefix(origin)
        if not url.startswith(origin + "/") or (path != "/" and not allowed_path.fullmatch(path)):
            raise SmokeError("Unexpected Sites probe route")
        req = Request(url, method="GET", headers={**headers, "cache-control": "no-cache", "accept-encoding": "identity"})
        try:
            response = opener.open(req, timeout=timeout)
        except HTTPError as response:
            # Inspect rather than follow root or authentication redirects.
            with response:
                yield response
        else:
            with response:
                yield response

    return get


def manifest(archive, expected):
    try:
        with tarfile.open(archive, "r:") as tar:
            entries = [m for m in tar if m.name == "manifest.json"]
            if len(entries) != 1 or not entries[0].isfile() or not 0 < entries[0].size <= 1024:
                raise SmokeError("Invalid visual manifest")
            with tar.extractfile(entries[0]) as entry:
                binary = entry.read(1025)
        if sha256(binary).hexdigest() != expected:
            raise SmokeError("Manifest digest mismatch")

        def unique(pairs):
            obj = dict(pairs)
            if len(pairs) != len(obj):
                raise SmokeError("Duplicate visual manifest entry")
            return obj

        files = json.loads(binary, object_pairs_hook=unique)
        if (type(files) is not dict or len(files) != 4 or "index.html" not in files
                or any(sum(bool(pattern.fullmatch(name)) for name in files) != 1 for pattern in ASSET_NAMES)
                or any(type(value) is not str or not DIGEST.fullmatch(value) for value in files.values())):
            raise SmokeError("Invalid visual manifest")
        return files
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError):
        raise SmokeError("Unreadable visual manifest") from None


def verify_sites_release(lane, archive, expected, get):
    """Use a trusted, authenticated no-redirect GET to assert the user path.

    `get(url, timeout=...)` yields a urllib-style response with final URL,
    status, multi-value headers, and bounded reads. Only same-origin paths
    derived from the authorized immutable archive are requested.
    """
    if lane not in ORIGINS or not isinstance(expected, str) or not DIGEST.fullmatch(expected):
        raise SmokeError("Invalid Sites verification target")
    if not callable(get):
        raise SmokeError("Sites transport unavailable")
    files = manifest(archive, expected)
    origin = ORIGINS[lane]
    prefix = f"/visual/{expected}/"

    def header(response, key):
        entries = response.headers.get_all(key, [])
        if len(entries) > 1:
            raise SmokeError("Ambiguous Sites response header")
        return entries[0] if entries else None

    def probe(path, required_status, name=None):
        url = origin + path
        try:
            with get(url, timeout=TIMEOUT) as response:
                if response.geturl() != url or response.status != required_status:
                    raise SmokeError("Unexpected Sites status or redirect")
                if (header(response, "x-memory-visual-source") is not None
                        or header(response, "x-memory-visual-error") is not None):
                    raise SmokeError("Sites served a fallback or degraded result")
                if header(response, "x-memory-visual-digest") != expected:
                    raise SmokeError("Sites digest differs from the approved release")
                cache = header(response, "cache-control") or ""
                if "no-store" not in {value.strip().lower() for value in cache.split(",")}:
                    raise SmokeError("Sites response is not marked no-store")
                if name is None:
                    if header(response, "location") != prefix:
                        raise SmokeError("Sites root selected a different release")
                    return
                content_types = {"js": "text/javascript", "css": "text/css", "html": "text/html"}
                mime = (header(response, "content-type") or "").split(";", 1)[0].strip().lower()
                if mime != content_types[name.rsplit(".", 1)[1]]:
                    raise SmokeError("Unexpected Sites asset type")
                data = response.read(MAX_BODY + 1)
                if len(data) > MAX_BODY or sha256(data).hexdigest() != files[name]:
                    raise SmokeError("Sites asset differs from the approved archive")
        except SmokeError:
            raise
        except Exception:
            raise SmokeError("Sites transport failed") from None

    probe("/", 302)
    probe(prefix, 200, "index.html")
    for name in sorted(set(files) - {"index.html"}):
        probe(prefix + name, 200, name)
    # A release promoted across requests must not count as verified.
    probe("/", 302)
    return {"stage": lane, "manifest_sha256": expected, "verified_assets": len(files)}
