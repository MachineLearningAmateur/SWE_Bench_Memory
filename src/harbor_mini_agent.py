"""Harbor mini-SWE-agent adapter for SWE-rebench images with custom XDG paths.

Harbor 0.23.0's installer assumes uv's tool bin directory is $HOME/.local/bin.
Some July SWE-rebench images set XDG_DATA_HOME to /workspace/.local/share, so
uv otherwise links the CLI under /workspace/.local/bin and setup exits 127.
Only installation is customized; Harbor's normal agent run and verifier remain.
"""

from __future__ import annotations

import shlex
from dataclasses import replace
from typing import override

from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from harbor.agents.model_connection import ResolvedModelConnection
from harbor.environments.base import BaseEnvironment


class PathSafeMiniSweAgent(MiniSweAgent):
    @property
    @override
    def model_connection(self) -> ResolvedModelConnection:
        access = super().model_connection
        if access.api_key is None:
            return access
        # MiniSweAgent passes MSWEA_API_KEY, but LiteLLM's openai/ route also
        # needs the provider-native variable inside the task container.
        return replace(access, env={**access.env, "OPENAI_API_KEY": access.api_key})

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(
            environment, ("curl", "bash", "build_tools", "git")
        )
        version_spec = f"=={self._version}" if self._version else ""
        package = shlex.quote(f"mini-swe-agent{version_spec}")
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if ! command -v uv >/dev/null 2>&1; then "
                "curl -LsSf https://astral.sh/uv/install.sh | sh; fi; "
                'if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi; '
                'export PATH="$HOME/.local/bin:$PATH"; '
                'export UV_TOOL_BIN_DIR="$HOME/.local/bin"; '
                "uv python install 3.12; "
                f"uv tool install --python 3.12 {package} "
                "--with litellm --with orjson --with fastapi; "
                '"$UV_TOOL_BIN_DIR/mini-swe-agent" --help'
            ),
        )
