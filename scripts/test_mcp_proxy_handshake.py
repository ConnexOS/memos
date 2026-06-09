"""测试 MCP 代理完整握手"""
import json
import subprocess
import sys
import time
import threading
from pathlib import Path

MEMOS_DIR = Path(r"D:\DevSpace\MEMOS")
VENV_PYTHON = str(MEMOS_DIR / "venv" / "Scripts" / "python.exe")

chunks = {"stdout": [], "stderr": []}
done = threading.Event()

def reader(stream, name):
    while not done.is_set():
        ch = stream.read(1)
        if not ch:
            break
        chunks[name].append(ch)

proc = subprocess.Popen(
    [VENV_PYTHON, "-m", "memos.hook_proxy", "--server", "http://localhost:8000"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=str(MEMOS_DIR),
)
t1 = threading.Thread(target=reader, args=(proc.stdout, "stdout"), daemon=True)
t2 = threading.Thread(target=reader, args=(proc.stderr, "stderr"), daemon=True)
t1.start(); t2.start()

def send(msg):
    body = json.dumps(msg).encode()
    proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    proc.stdin.flush()

def read(timeout=5):
    deadline = time.time() + timeout
    hdr = b""
    while time.time() < deadline:
        b = proc.stdout.read(1)
        if not b:
            break
        hdr += b
        if hdr.endswith(b"\r\n\r\n"):
            break
    for line in hdr.decode("utf-8", "replace").strip().split("\r\n"):
        if line.lower().startswith("content-length"):
            cl = int(line.split(":")[1].strip())
            return json.loads(proc.stdout.read(cl).decode("utf-8"))
    return None

# 测试快速启动
print("1. 启动时间:", flush=True)
time.sleep(1)
stdout_b = len(b"".join(chunks["stdout"]))
stderr_b = len(b"".join(chunks["stderr"]))
print(f"   1s 后 stdout={stdout_b}B stderr={stderr_b}B", flush=True)

# 全流程测试
print("\n2. initialize →", end=" ", flush=True)
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}})
r = read()
assert r and "result" in r, f"initialize failed: {r}"
print(f"ok (id={r['id']}, proto={r['result']['protocolVersion']})", flush=True)

print("3. tools/list →", end=" ", flush=True)
chunks["stdout"].clear()
send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
r = read()
assert r and "result" in r, f"tools/list failed: {r}"
tools = r["result"]["tools"]
print(f"ok, {len(tools)} tools", flush=True)

print("4. ping →", end=" ", flush=True)
chunks["stdout"].clear()
send({"jsonrpc":"2.0","id":3,"method":"ping"})
r = read()
assert r and "result" in r, f"ping failed: {r}"
print("ok", flush=True)

done.set()
proc.terminate()
proc.wait()
print("\n✓ 全部通过", flush=True)
