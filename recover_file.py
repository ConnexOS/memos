"""Recover garbled test file"""
with open('tests/test_integration_all.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
with open('tests/test_integration_all.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Recovered:', len(content), 'chars')
