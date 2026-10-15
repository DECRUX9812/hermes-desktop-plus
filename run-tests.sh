#!/bin/bash
set -e
cd /home/user/Code/ship-test/wt/s18

echo "=== Installing hermes_plus ==="
flock /tmp/hermes-build.lock .venv/bin/pip install -e 'packages/hermes_plus[test]'

echo ""
echo "=== TEST 1: profiles tests only ==="
flock /tmp/hermes-build.lock .venv/bin/pytest packages/hermes_plus/tests/contract/test_b1_stock.py -k profiles -v -rA

echo ""
echo "=== TEST 2: whole contract file ==="
flock /tmp/hermes-build.lock .venv/bin/pytest packages/hermes_plus/tests/contract/test_b1_stock.py -v --tb=short

echo ""
echo "=== TEST 3: test_rpc_registry.py ==="
flock /tmp/hermes-build.lock .venv/bin/pytest packages/hermes_plus/tests/test_rpc_registry.py -v --tb=short

echo ""
echo "=== TEST 4: tests/unit ==="
flock /tmp/hermes-build.lock .venv/bin/pytest packages/hermes_plus/tests/unit -v --tb=short

echo ""
echo "All tests completed!"
