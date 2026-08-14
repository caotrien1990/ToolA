"""
MCP Client Manager: Test kết nối sub-MCP server, harness test 1 (đơn giản hóa, dễ debug)
"""
import asyncio
import sys
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def main():
    project_path = sys.argv[1] if len(sys.argv) > 1 else "."
    server_params = StdioServerParameters(
        command="npx",
        args=["@redf0x1/mcp-server-filesystem", "--cwd", project_path, "--mcp"]
    )
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("Tools:", [tool.name for tool in tools.tools])
                # Đọc thử file requirements.txt
                try:
                    result = await session.call_tool("read_file", {"path": "requirements.txt"})
                    print("read_file result:", result)
                except Exception as e:
                    print("read_file error:", e)
    except Exception as e:
        print("Connection or server error:", e)

if __name__ == "__main__":
    asyncio.run(main())
