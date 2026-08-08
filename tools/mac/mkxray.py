import os, sys, json, base64, urllib.parse, urllib.request, ssl
sub = os.environ.get("VLESS_URL")
if not sub: sys.exit("нет VLESS_URL")
ctx = ssl.create_default_context()
try: ctx.load_verify_locations("/root/.ccr/ca-bundle.crt")
except Exception: pass
body = urllib.request.urlopen(urllib.request.Request(sub, headers={"User-Agent":"v2rayNG/1.8.5"}), timeout=60, context=ctx).read().decode("utf-8","replace")
lines = [l.strip() for l in body.splitlines() if l.strip().startswith("vless://")]
want = sys.argv[1].lower()
picked = None
for l in lines:
    frag = urllib.parse.unquote(l.split("#",1)[1]) if "#" in l else ""
    if want in frag.lower(): picked = (l, frag); break
if not picked: sys.exit("не нашёл выход по метке %r; всего строк %d" % (want, len(lines)))
link, frag = picked
u = urllib.parse.urlsplit(link.split("#",1)[0])
q = dict(urllib.parse.parse_qsl(u.query))
cfg = {
  "log": {"loglevel": "info", "error": "/Users/agent/xray/err.log"},
  "inbounds": [{"tag":"socks","port":int(sys.argv[2]),"listen":"127.0.0.1","protocol":"socks",
                "settings":{"udp":False,"auth":"noauth"},"sniffing":{"enabled":True,"destOverride":["http","tls"]}}],
  "outbounds": [{"tag":"proxy","protocol":"vless","settings":{"vnext":[{
      "address": u.hostname, "port": u.port or 443,
      "users":[{"id": u.username, "encryption": q.get("encryption","none"), "flow": q.get("flow","")}]}]},
    "streamSettings":{"network": q.get("type","ws"), "security": q.get("security","tls"),
      "tlsSettings":{"serverName": q.get("sni") or q.get("host") or u.hostname, "allowInsecure": False},
      "wsSettings":{"path": urllib.parse.unquote(q.get("path","/")), "headers":{"Host": q.get("host") or u.hostname}}}}]
}
open(sys.argv[3],"w").write(json.dumps(cfg, ensure_ascii=False, indent=1))
print("метка выхода: %s | транспорт %s | порт %s | конфиг: %s" % (frag, q.get("type"), u.port or 443, sys.argv[3]))
