"""快速验证 MCP 代理 - 二进制模式"""
import json, subprocess, sys

PY = r"D:\DevSpace\MEMOS\venv\Scripts\python.exe"
CWD = r"D:\DevSpace\MEMOS"

init = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}})
lt = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list"})
ping = json.dumps({"jsonrpc":"2.0","id":3,"method":"ping"})

input_bytes = b""
for msg in [init, lt, ping]:
    body = msg.encode()
    input_bytes += f"Content-Length: {len(body)}\r\n\r\n".encode() + body

proc = subprocess.run(
    [PY, "-m", "memos.hook_proxy", "--server", "http://localhost:8000"],
    input=input_bytes,
    capture_output=True,
    cwd=CWD,
    timeout=30,
)

# Parse stdout frames
data = proc.stdout
idx = 0
frame_num = 0
while idx < len(data):
    # Find Content-Length header
    header_end = data.find(b"\r\n\r\n", idx)
    if header_end == -1:
        print(f"Partial data at offset {idx}: {data[idx:].hex()[:50]}")
        break
    header = data[idx:header_end].decode()
    cl = int([l for l in header.split("\r\n") if "content-length" in l.lower()][0].split(":")[1])
    body_start = header_end + 4
    body = data[body_start:body_start+cl]
    frame_num += 1
    resp = json.loads(body.decode())
    if "result" in resp:
        if "tools" in resp["result"]:
            print(f"Frame {frame_num}: tools/list -> {len(resp['result']['tools'])} tools")
        else:
            print(f"Frame {frame_num}: {list(resp['result'].keys())}")
    elif "error" in resp:
        print(f"Frame {frame_num}: ERROR {resp['error']['code']} {resp['error']['message'][:60]}")
    idx = body_start + cl

print(f"\nTotal: {frame_num} frames, {len(proc.stderr)} bytes stderr")
