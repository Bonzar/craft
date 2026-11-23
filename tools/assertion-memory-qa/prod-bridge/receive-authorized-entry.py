import sys
sys.path.insert(0, '/etc/assertion-release/production/authorization')
sys.path.insert(0, '/etc/assertion-release/shared')
from receive_authorized import main
raise SystemExit(main())
