"""Snowflake MCP server — exposes Snowflake tools over stdio."""

from __future__ import annotations

import json
import sys

from openjarvis.mcp.protocol import MCPRequest
from openjarvis.mcp.server import MCPServer
from openjarvis.tools.snowflake_query import SnowflakeQueryTool


def main() -> None:
    server = MCPServer(tools=[SnowflakeQueryTool()])

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "id" not in payload:
            # MCP notification; no response required
            continue

        req = MCPRequest(
            method=payload["method"],
            params=payload.get("params", {}),
            id=payload["id"],
        )
        response = server.handle(req)
        print(response.to_json(), flush=True)


if __name__ == "__main__":
    main()
