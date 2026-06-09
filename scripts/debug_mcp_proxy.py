"""诊断 MCP 代理 stdio 输出 - 使用 venv Python"""
import json
import subprocess
import sys
import time
import threading
from pathlib import Path

MEMOS_DIR = Path(r"D:\DevSpace\MEMOS")
VENV_PYTHON = str(MEMOS_DIR / "venv" / "Scripts" / "python.exe")

stdout_chunks = []
stderr_chunks = []
done = threading.Event()

def reader(stream, chunks):
    while not done.is_set():
        ch = stream.read(1)
        if not ch:
            break
        chunks.append(ch)

proc = subprocess.Popen(
    [VENV_PYTHON, "-m", "memos.hook_proxy", "--server", "http://localhost:8000"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd=str(MEMOS_DIR),
)

t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks), daemon=True)
t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks), daemon=True)
t_out.start()
t_err.start()

print(f"代理 PID: {proc.pid}", flush=True)
print("等待启动...", flush=True)
time.sleep(5)

stderr_data = b"".join(stderr_chunks)
stdout_data = b"".join(stdout_chunks)
print(f"启动后 stderr: {len(stderr_data)} bytes", flush=True)
print(f"启动后 stdout: {len(stdout_data)} bytes", flush=True)
if stderr_data:
    print(f"stderr 内容:\n{stderr_data.decode('utf-8', 'replace')[:1000]}", flush=True)

# 发送 initialize 请求
print("\n>> 发送 initialize...", flush=True)
init_body = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}},
}).encode()
frame = b"Content-Length: " + str(len(init_body)).encode() + b"\r\n\r\n" + init_body
proc.stdin.write(frame)
proc.stdin.flush()
print(f"  已发送 {len(frame)} bytes", flush=True)

time.sleep(5)

stderr_data = b"".join(stderr_chunks)
stdout_data = b"".join(stdout_chunks)
print(f"\ninitialize 后 stderr: {len(stderr_data)} bytes", flush=True)
print(f"initialize 后 stdout: {len(stdout_data)} bytes", flush=True)
if stdout_data:
    print(f"stdout hex: {stdout_data.hex()[:500]}", flush=True)
    print(f"stdout text: {stdout_data.decode('utf-8', 'replace')[:500]}", flush=True)
if stderr_data:
    print(f"stderr text:\n{stderr_data.decode('utf-8', 'replace')[:1000]}", flush=True)

done.set()
proc.terminate()
proc.wait()
print(f"\n返回码: {proc.returncode}", flush=True)
