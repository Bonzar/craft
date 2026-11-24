"""Stage one exact TEST route on an independently approved settled nginx baseline."""
import argparse, hmac, json, os, re
from pathlib import Path
from stage_nginx_pair import PORTS, insert_location, read_staged, sha, stage_file

def plan_settled(original, expected):
    if not re.fullmatch(r'[a-f0-9]{64}', expected) or not hmac.compare_digest(sha(original),expected):
        raise ValueError('settled baseline digest mismatch')
    for old,new in PORTS:
        if old in original or original.count(new)!=1:
            raise ValueError('not the approved settled PROD upstream layout')
    proposed,fragment=insert_location(original)
    if len(proposed)>=128000 or proposed.count(fragment)!=1:
        raise ValueError('proposal outside staging bounds')
    offset=proposed.index(fragment)
    prefix,suffix=proposed[:offset],proposed[offset+len(fragment):]
    if prefix+suffix!=original:
        raise ValueError('not an exact one-location addition')
    return proposed,fragment,dict(prefix_sha256=sha(prefix),suffix_sha256=sha(suffix),unchanged_locations_bytes_sha256=sha(prefix+suffix))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--settled',type=Path,required=True)
    p.add_argument('--expected-settled-sha256',required=True)
    p.add_argument('--new-stage-dir',type=Path,required=True)
    a=p.parse_args()
    original=read_staged(a.settled)
    proposed,fragment,proof=plan_settled(original,a.expected_settled_sha256)
    os.mkdir(a.new_stage_dir,0o700)
    files={'settled.original':original,'settled.proposed':proposed,'test.fragment':fragment}
    for name,data in files.items():
        stage_file(a.new_stage_dir/name,data)
    print(json.dumps(dict(proof=proof,sha256={name:sha(data) for name,data in files.items()},test_paths_added=1,prod_upstream_substitutions=0),sort_keys=True))
if __name__=='__main__':
    main()
