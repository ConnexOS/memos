"""MCP 客户端集成测试 — 通过 stdio JSON-RPC 调用"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = "src/memos/server.py"


async def test():
    server_params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT], cwd="D:/DevSpace/MEMOS")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.list_tools()
            print("可用工具:")
            for tool in result.tools:
                print(f"  - {tool.name}: {tool.description}")
                print(f"    参数: {tool.inputSchema}")

            result = await session.call_tool("remember", {"text": "项目使用FastAPI框架，端口8000"})
            print(f"\nremember 结果: {result.content[0].text}")

            result = await session.call_tool("recall", {"query": "我用的什么后端框架？"})
            print(f"\nrecall 结果: {result.content[0].text}")


if __name__ == "__main__":
    asyncio.run(test())
