"""Independent marked-fixture temporal oracle CLI; emits only counts/digests."""
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from source_oracle import expected_slice
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--mode',choices=['current','known_then','known_now'],required=True);p.add_argument('--at',required=True);p.add_argument('--history',choices=['0','1'],default='1');p.add_argument('--undated',choices=['separate','include','only'],default='separate');a=p.parse_args();m=json.loads(a.manifest.read_text());r=expected_slice(m,mode=a.mode,at=a.at,history=a.history=='1',undated=a.undated,captured_at=datetime.now(timezone.utc).isoformat(),explicit_cancellation=True);print(json.dumps({'counts':r['counts'],'undated_known_count':len(r['undated_ids']),'ids_sha256':r['ids_sha256']}))
