"""
Example CodeAssist Plugin

This demonstrates how to create a plugin that adds custom tools.
"""

import logging
from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class WeatherTool(Tool):
    """Example tool that fetches weather information."""

    name = "weather"
    description = "Get current weather information for a location (demo tool)"
    parameters = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name or location",
            },
        },
        "required": ["location"],
    }

    async def execute(self, location: str) -> ToolResult:
        # This is a demo tool - in reality you'd call a weather API
        return ToolResult(
            output=f"**Weather for {location}** (demo)\n\n"
                   f"Temperature: 72°F (22°C)\n"
                   f"Conditions: Partly Cloudy\n"
                   f"Humidity: 45%\n"
                   f"Wind: 8 mph NW"
        )


def register(config: dict) -> dict:
    """
    Register plugin tools and hooks.

    Args:
        config: Plugin configuration from plugin.json

    Returns:
        Dictionary with 'tools' and optional 'hooks'
    """
    tools = [WeatherTool()]

    return {
        "tools": tools,
        "hooks": {
            # Example hooks (not implemented in this demo)
            # "on_tool_execute": [my_hook_function],
            # "on_session_start": [another_hook],
        },
    }
