"""weather-mcp server. Exposes marine weather tools to any MCP client over stdio.

Cache path comes from WEATHER_CACHE_PATH (default ~/.weather-mcp/cache.sqlite).
Stormglass API key from STORMGLASS_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from weather_mcp.cache import EventCache
from weather_mcp.client import RateLimitedClient
from weather_mcp.quota import StormglassQuota
from weather_mcp.tools import (
    get_marine_forecast,
    get_marine_forecast_premium,
    get_nearest_buoy_observations_tool,
    get_stormglass_quota_status,
)

logger = logging.getLogger(__name__)

TOOL_NAMES = [
    "get_marine_forecast",
    "get_marine_forecast_premium",
    "get_nearest_buoy_observations",
    "get_stormglass_quota_status",
]


async def dispatch(
    client: RateLimitedClient,
    cache: EventCache,
    quota: StormglassQuota,
    name: str,
    args: dict,
) -> dict:
    if name == "get_marine_forecast":
        return await get_marine_forecast(
            client, cache,
            lat=args["lat"], lon=args["lon"],
            hours_ahead=args.get("hours_ahead", 12),
        )
    if name == "get_marine_forecast_premium":
        return await get_marine_forecast_premium(
            client, cache, quota,
            lat=args["lat"], lon=args["lon"],
            hours_ahead=args.get("hours_ahead", 24),
        )
    if name == "get_nearest_buoy_observations":
        return await get_nearest_buoy_observations_tool(
            client, cache,
            lat=args["lat"], lon=args["lon"],
            max_distance_nm=args.get("max_distance_nm", 50.0),
            limit=args.get("limit", 3),
        )
    if name == "get_stormglass_quota_status":
        return get_stormglass_quota_status(quota)
    raise ValueError(f"Unknown tool: {name}")


def build_server(
    client: RateLimitedClient, cache: EventCache, quota: StormglassQuota
) -> Server:
    server = Server("weather-mcp")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="get_marine_forecast",
                description=(
                    "Open-Meteo wind, separated swell, wind-waves, and pressure for a position. "
                    "Routine forecast; no quota. Returns hourly entries with display strings."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude (decimal degrees)."},
                        "lon": {"type": "number", "description": "Longitude (decimal degrees)."},
                        "hours_ahead": {
                            "type": "integer",
                            "description": "How many hours of forecast to return (default 12).",
                        },
                    },
                    "required": ["lat", "lon"],
                },
            ),
            types.Tool(
                name="get_marine_forecast_premium",
                description=(
                    "Stormglass blended marine forecast. Costs 1 of 10 daily tokens "
                    "(free tier) on cache miss. Call get_stormglass_quota_status first."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude (decimal degrees)."},
                        "lon": {"type": "number", "description": "Longitude (decimal degrees)."},
                        "hours_ahead": {
                            "type": "integer",
                            "description": "How many hours of forecast (default 24).",
                        },
                    },
                    "required": ["lat", "lon"],
                },
            ),
            types.Tool(
                name="get_nearest_buoy_observations",
                description=(
                    "NDBC observed wind and combined waves from the nearest reporting buoys. "
                    "Use to reality-check the forecast. Note: standard NDBC files do not "
                    "separate swell from wind waves."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude (decimal degrees)."},
                        "lon": {"type": "number", "description": "Longitude (decimal degrees)."},
                        "max_distance_nm": {
                            "type": "number",
                            "description": "Skip buoys farther than this (default 50 nm).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max number of stations to return (default 3).",
                        },
                    },
                    "required": ["lat", "lon"],
                },
            ),
            types.Tool(
                name="get_stormglass_quota_status",
                description=(
                    "Premium tokens used/remaining for the current UTC day. "
                    "Call before any get_marine_forecast_premium request."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, args: dict | None) -> list[types.TextContent]:
        result = await dispatch(client, cache, quota, name, args or {})
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


def main() -> None:
    cache_path = os.environ.get(
        "WEATHER_CACHE_PATH", str(Path.home() / ".weather-mcp" / "cache.sqlite")
    )
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    cache = EventCache(cache_path)
    cache.init_schema()
    quota = StormglassQuota(cache.conn)
    quota.init_schema()
    client = RateLimitedClient()
    server = build_server(client, cache, quota)

    async def _run() -> None:
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream, write_stream, server.create_initialization_options()
                )
        finally:
            await client.aclose()
            cache.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
