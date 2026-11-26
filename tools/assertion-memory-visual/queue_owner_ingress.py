"""Render the fixed owner queue routes for either lane; never read key values."""
import ipaddress
import re

CONTROLS = ('pause', 'sessions', 'items/source-preview', 'items/remove', 'database/clear')
METHODS = (('POST', '/prepare'), ('PUT', ''))
HEADERS = ('Cookie', 'Origin', 'Content-Type', 'X-CSRF-Token', 'Sec-Fetch-Site')
HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z')
INTERNAL = re.compile(r'/[A-Za-z0-9_/-]+\Z')
CONFIG_FILE = re.compile(r'/etc/nginx/[A-Za-z0-9_/-]+\.conf\Z')
PORT = {'test': 8829, 'prod': 8830}


def owner_routes(*, lane, host, backend_ip, prefix, owner_auth, marker_include):
    """Return additive Gateway path definitions and a server-context nginx fragment.

    The caller must keep the Gateway's existing equivalent pause paths, or replace
    them with these definitions atomically, never publish duplicates. Owner-auth
    and both existing headers must be provided by a trusted per-lane root config.
    """
    ip = ipaddress.IPv4Address(backend_ip)
    if (lane not in PORT or not HOST.fullmatch(host) or len(host)>253
            or not INTERNAL.fullmatch(prefix) or prefix.endswith('/') or '//' in prefix
            or not INTERNAL.fullmatch(owner_auth) or owner_auth.endswith('/') or '//' in owner_auth
            or not CONFIG_FILE.fullmatch(marker_include) or '//' in marker_include
            or owner_auth.startswith(prefix+'/')):
        raise ValueError('Invalid owner ingress')
    gateway = {}
    nginx = []
    for control in CONTROLS:
        for method, suffix in METHODS:
            browser_path = '/api/v1/processing/'+control+suffix
            proxy_path = prefix+browser_path
            names = HEADERS if method=='PUT' else tuple(h for h in HEADERS if h!='X-CSRF-Token')
            gateway[browser_path] = {method.lower(): {
                'parameters': [{'name':h, 'in':'header', 'required':False, 'schema':{'type':'string'}} for h in names],
                'responses': {'200': {'description':'Authenticated Visual queue operation'}},
                'x-yc-apigateway-integration': {
                    'type':'http', 'url':f'https://{ip}{proxy_path}',
                    'omitEmptyHeaders':True, 'timeouts':{'connect':3,'read':22},
                    'headers':{name:'{'+name+'}' for name in names}
                }
            }}
            nginx.append(f"""location = {proxy_path} {{
  if ($request_method != {method}) {{ return 405; }}
  if ($args != "") {{ return 404; }}
  if ($http_origin != "https://{host}") {{ return 403; }}
  auth_request {owner_auth};
  client_max_body_size 20k;
  proxy_pass_request_headers off;
  proxy_set_header Host {host};
  proxy_set_header Origin $http_origin;
  proxy_set_header Sec-Fetch-Site $http_sec_fetch_site;
  proxy_set_header Content-Type $http_content_type;
  proxy_set_header X-CSRF-Token $http_x_csrf_token;
  include {marker_include};
  proxy_cache off;
  proxy_redirect off;
  proxy_hide_header Set-Cookie;
  proxy_pass http://127.0.0.1:{PORT[lane]}{browser_path};
}}
""")
    return gateway, ''.join(nginx)
