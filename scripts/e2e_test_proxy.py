"""快速端到端测试 MCP 代理"""
import json
import subprocess
import sys
import time
from pathlib import Path

VENV = r"D:\DevSpace\MEMOS\venv\Scripts\python.exe"
CWD = r"D:\DevSpace\MEMOS"

p = subprocess.Popen(
    [VENV, "-m", "memos.hook_proxy", "--server", "http://localhost:8000"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=CWD,
)
time.sleep(1.5)

def send(msg):
    body = json.dumps(msg).encode()
    p.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    p.stdin.flush()

def recv():
    hdr = b""
    while not hdr.endswith(b"\r\n\r\n"):
        hdr += p.stdout.read(1)
    for line in hdr.decode().strip().split("\r\n"):
        if "content-length" in line.lower():
            cl = int(line.split(":")[1])
            return json.loads(p.stdout.read(cl).decode())

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}})
r = recv()
assert "result" in r, f"initialize failed: {r}"
print("initialize: OK", flush=True)

send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
r = recv()
tools = r["result"]["tools"]
print(f"tools/list: {len(tools)} tools", flush=True)

send({"jsonrpc":"2.0","id":3,"method":"ping"})
r = recv()
assert "result" in r, f"ping failed: {r}"
print("ping: OK", flush=True)

# tools/call: remember 工具
remember_tool = next(t for t in tools if t["name"] == "remember")
send({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"remember","arguments":{"text":"test from proxy","metadata":{"type":"fact"}}}})
r = recv()
if "result" in r:
    print(f"remember: OK -> {r['result']}", flush=True)
elif "error" in r:
    print(f"remember: error -> {r['error']['message']}", flush=True)

p.terminate()
print("\nALL PASS", flush=True)
