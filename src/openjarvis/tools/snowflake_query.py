"""Snowflake query tool — execute read-only SQL against Snowflake."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.db_query import _format_table, _is_read_only_query


@ToolRegistry.register("snowflake_query")
class SnowflakeQueryTool(BaseTool):
    """Execute a read-only SQL query against a Snowflake warehouse."""

    tool_id = "snowflake_query"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="snowflake_query",
            description=(
                "Execute a read-only SQL query against Snowflake."
                " Credentials are read from SNOWFLAKE_* environment variables."
                " Requires: pip install snowflake-connector-python"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute.",
                    },
                    "account": {
                        "type": "string",
                        "description": "Snowflake account identifier.",
                    },
                    "warehouse": {
                        "type": "string",
                        "description": "Snowflake warehouse to use.",
                    },
                    "database": {
                        "type": "string",
                        "description": "Database to query.",
                    },
                    "schema": {
                        "type": "string",
                        "description": "Schema to query.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Snowflake role to assume.",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Maximum rows to return. Default: 100.",
                    },
                    "read_only": {
                        "type": "boolean",
                        "description": "Restrict to read-only queries. Default: true.",
                    },
                },
                "required": ["query"],
            },
            category="database",
            timeout_seconds=60.0,
            required_capabilities=["database:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        query: str = params.get("query", "")
        if not query:
            return ToolResult(
                tool_name="snowflake_query",
                content="No query provided.",
                success=False,
            )

        read_only: bool = params.get("read_only", True)
        if read_only and not _is_read_only_query(query):
            return ToolResult(
                tool_name="snowflake_query",
                content=(
                    "Query blocked: only SELECT, EXPLAIN, PRAGMA,"
                    " DESCRIBE, SHOW, and WITH...SELECT are allowed"
                    " in read-only mode."
                ),
                success=False,
            )

        try:
            import snowflake.connector  # type: ignore[import]
        except ImportError:
            return ToolResult(
                tool_name="snowflake_query",
                content=(
                    "Snowflake support requires the snowflake-connector-python package."
                    " Install it with: pip install snowflake-connector-python"
                ),
                success=False,
            )

        conn_args = self._build_connection_args(params)
        if not conn_args.get("account") or not conn_args.get("user"):
            return ToolResult(
                tool_name="snowflake_query",
                content=(
                    "Snowflake account and user are required."
                    " Set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER environment variables,"
                    " or pass them in the connection config."
                ),
                success=False,
            )

        conn = None
        try:
            conn = snowflake.connector.connect(**conn_args)
            cursor = conn.cursor()
            cursor.execute(query)

            column_names: List[str] = []
            if cursor.description:
                column_names = [desc[0] for desc in cursor.description]

            max_rows: int = params.get("max_rows", 100)
            rows = cursor.fetchmany(max_rows) if column_names else []
            row_count = len(rows)

            if column_names:
                content = _format_table(column_names, rows)
            else:
                content = (
                    f"Query executed successfully. Rows affected: {cursor.rowcount}"
                )

            return ToolResult(
                tool_name="snowflake_query",
                content=content,
                success=True,
                metadata={
                    "row_count": row_count,
                    "column_names": column_names,
                    "db_type": "snowflake",
                },
            )
        except Exception as exc:
            return ToolResult(
                tool_name="snowflake_query",
                content=f"Snowflake error: {exc}",
                success=False,
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _build_connection_args(self, params: Dict[str, Any]) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "account": os.getenv("SNOWFLAKE_ACCOUNT"),
            "user": os.getenv("SNOWFLAKE_USER"),
            "password": os.getenv("SNOWFLAKE_PASSWORD"),
            "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
            "database": os.getenv("SNOWFLAKE_DATABASE"),
            "schema": os.getenv("SNOWFLAKE_SCHEMA"),
            "role": os.getenv("SNOWFLAKE_ROLE"),
        }
        authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR")
        if authenticator:
            args["authenticator"] = authenticator

        for key in ("account", "warehouse", "database", "schema", "role"):
            value = params.get(key)
            if value:
                args[key] = value

        return {k: v for k, v in args.items() if v not in (None, "")}


__all__ = ["SnowflakeQueryTool"]
