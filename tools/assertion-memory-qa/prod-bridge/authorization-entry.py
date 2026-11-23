"""Installed by root alongside the immutable authorizer modules."""
import sys
sys.path.insert(0, '/etc/assertion-release/production/authorization')
from authorizer import main
sys.argv[:] = [sys.argv[0], '/etc/assertion-release/production/authorization-policy.json']
main()
