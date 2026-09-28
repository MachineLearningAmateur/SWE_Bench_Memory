"""Harbor mini-SWE-agent adapter for SWE-rebench images with custom XDG paths.

Harbor 0.23.0's installer assumes uv's tool bin directory is $HOME/.local/bin.
Some July SWE-rebench images set XDG_DATA_HOME to /workspace/.local/share, so
uv otherwise links the CLI under /workspace/.local/bin and setup exits 127.
The repairing subclass also supplies a narrow model adapter. Harbor's normal
agent run and official verifier remain unchanged.
"""

from __future__ import annotations

import shlex
import uuid
from dataclasses import replace
from pathlib import Path
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


class RepairingMiniSweAgent(PathSafeMiniSweAgent):
    """Use the predeclared native tool-call repair with Harbor's normal agent."""

    _tool_model_dir = "/tmp/mswea-gpt-oss-model"

    @property
    @override
    def model_connection(self) -> ResolvedModelConnection:
        access = super().model_connection
        return replace(access, env={**access.env, "PYTHONPATH": self._tool_model_dir})

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        model_source = Path(__file__).with_name("gpt_oss_tool_model.py").read_text(
            encoding="utf-8"
        )
        marker = f"MSWEA_TOOL_MODEL_EOF_{uuid.uuid4().hex}"
        await self.exec_as_agent(
            environment,
            command=(
                f"mkdir -p {shlex.quote(self._tool_model_dir)}\n"
                f"cat > {shlex.quote(self._tool_model_dir + '/gpt_oss_tool_model.py')} "
                f"<<'{marker}'\n{model_source}\n{marker}\n"
            ),
        )
