#!/usr/bin/env python3
"""
CI SEO Agent — Standalone MCP stdio server.
Invoked by Claude Code via .mcp.json command.
Communicates over stdin/stdout using the MCP protocol.
"""
import sys
import os

# Add agent dir to path
sys.path.insert(0, os.path.dirname(__file__))

# Load env before importing config
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Import and run the mcp server in stdio mode
from mcp_server import mcp

if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
