"""
Shared settings for launching the mcp-atlassian MCP server.

Every script in this folder starts the server the same way, so the code lives
here once instead of being copied into each script.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from mcp import StdioServerParameters


# Read backend/.env into os.environ. We use the path next to this file so the
# scripts work no matter which folder you run them from.
# mcp-atlassian reads JIRA_URL, JIRA_USERNAME and JIRA_API_TOKEN from its
# environment, so they must be in os.environ before we launch it.
load_dotenv(Path(__file__).parent / ".env")


def get_server_params() -> StdioServerParameters:
    """Describe how to launch the MCP server as a child process (process launch).

    An MCP server over stdio is just a normal program. We (the MCP *client*)
    start it ourselves: "uvx mcp-atlassian" downloads the package the first
    time, then runs it.
    """
    # We pass the whole os.environ (not only the Jira vars) so the child also
    # gets PATH and other system variables it needs to start.
    server_env = dict(os.environ)

    # Windows fixes for this machine (harmless elsewhere):
    # - UV_NATIVE_TLS makes uvx trust the Windows certificate store. Without it,
    #   downloading mcp-atlassian fails with "invalid peer certificate" when
    #   security software inspects HTTPS traffic.
    # - SSLKEYLOGFILE (set by network-monitoring software) makes OpenSSL inside
    #   the server crash with "no OPENSSL_Applink", so we remove it for the child.
    server_env.setdefault("UV_NATIVE_TLS", "true")
    server_env.pop("SSLKEYLOGFILE", None)

    return StdioServerParameters(
        command="uvx",
        args=["mcp-atlassian"],
        env=server_env,
    )
