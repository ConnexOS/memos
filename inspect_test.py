"""Read current test file and print line numbers for targeted replacement"""
with open('tests/test_integration_all.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    stripped = line.rstrip()
    # Skip LongTermMemory inside imports
    if 'LongTermMemory' in stripped and 'from' not in stripped and 'import' not in stripped:
        print(f"Line {i}: {stripped[:120]}")
    # Print class and method names
    if stripped.startswith('class ') or stripped.startswith('    def test_'):
        print(f"Line {i}: {stripped[:120]}")
