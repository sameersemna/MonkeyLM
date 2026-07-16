#!/usr/bin/env python3
"""Check that all symbols referenced in tests are exported by the shim."""

import ast
import re

# Get symbols from tests
with open('tests/test_monkey_agent_advanced.py', 'r') as f:
    test_content = f.read()

test_symbols = set()
# Use word boundary to avoid partial matches like random.random()
for match in re.findall(r'\b(m\.[a-zA-Z_][a-zA-Z0-9_]*)\b', test_content):
    test_symbols.add(match.replace('m.', ''))

print(f"Test symbols ({len(test_symbols)}):", sorted(test_symbols))

# Get symbols from shim
with open('monkey_agent_advanced.py', 'r') as f:
    shim_content = f.read()

# Parse the shim to find all imports
shim_symbols = set()
tree = ast.parse(shim_content)

for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom):
        module = node.module
        for alias in node.names:
            shim_symbols.add(alias.name)
    elif isinstance(node, ast.FunctionDef):
        shim_symbols.add(node.name)
    elif isinstance(node, ast.ClassDef):
        shim_symbols.add(node.name)

# Add mutable globals defined in shim (module-level assignments)
for line in shim_content.split('\n'):
    stripped = line.strip()
    if stripped and not stripped.startswith('    ') and not stripped.startswith('#'):
        if '=' in stripped and not stripped.startswith('def ') and not stripped.startswith('class '):
            parts = stripped.split('=')
            if len(parts) >= 2:
                var_part = parts[0].split(':')[0].strip()
                # Check if it looks like a variable name
                if var_part and (var_part.isupper() or (var_part.isidentifier() and not var_part.startswith('_'))):
                    shim_symbols.add(var_part)

print(f"\nShim symbols ({len(shim_symbols)}):", sorted(shim_symbols))
print("\nMissing from shim:", test_symbols - shim_symbols)
print("\nExtra in shim:", shim_symbols - test_symbols)
