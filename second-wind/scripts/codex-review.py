#!/usr/bin/env python3
"""Print per-run overrides disabling the Codex workspace's MCP servers."""
import json
import re
import subprocess
import sys


def main():
    try:
        result = subprocess.run(['codex', 'mcp', 'list', '--json'],
                                capture_output=True, text=True, timeout=30)
        servers = json.loads(result.stdout)
        if result.returncode or not isinstance(servers, list):
            raise ValueError('client did not return a server list')
        overrides = ['apps._default.enabled=false']
        for server in servers:
            name = server['name']
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                raise ValueError('server name cannot safely be overridden')
            overrides.append('mcp_servers.%s.enabled=false' % name)
        print('\n'.join(overrides))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('second-wind: cannot list Codex MCP servers; refusing an unverified review boundary', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
