import sys
sys.path.insert(0, '/etc/assertion-release/production/authorization')
from receive_authorized import main
raise SystemExit(main())
