from stage_nginx_pair import insert_location,sha
from stage_nginx_guarded_test_effects import derive,plan
base=b'''server {
    location = /mcp {
        proxy_pass http://127.0.0.1:8803;
    }
    location = /memory-graph-read {
        proxy_pass http://127.0.0.1:8818;
    }
    location = /memory-mcp-test {
        proxy_pass http://127.0.0.1:8893;
    }
}
'''
approved=derive(base,'approved');frozen=derive(base,'frozen')
live,_=insert_location(approved.replace(b'    location = /memory-mcp-test',b'    location = /independently-reviewed-other { proxy_pass http://127.0.0.1:8818; }\n    location = /memory-mcp-test',1))
review=dict(live=sha(live),baseline=sha(base),approved=sha(approved),frozen=sha(frozen),
            live_prod_routes={'/mcp':'approved','/memory-graph-read':'approved'})
assert tuple(live.count(x) for x in (b'8803',b'8818',b'8903',b'8918'))==(0,1,1,1)
out,fragment=plan(live,base,approved,frozen,review)
assert out.replace(fragment,b'',1)==live and b'8834' in fragment
tests=[(live+b'\n',base,approved,frozen,review),
       (live,base,approved,frozen+b'\n',review|{'frozen':sha(frozen+b'\n')}),
       (live,base,approved,frozen,review|{'live_prod_routes':{'/mcp':'baseline','/memory-graph-read':'approved'}}),
       (live,base,approved,frozen,review|{'live_prod_routes':{'/mcp':'frozen','/memory-graph-read':'approved'}})]
for args in tests:
 try:plan(*args)
 except ValueError:pass
 else:raise AssertionError('unsafe')
print('Exact 0/1/1/1 case plus four drift/frozen/route state negatives pass; only new TEST bytes staged')
