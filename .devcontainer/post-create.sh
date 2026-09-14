#!/usr/bin/env bash
set -euo pipefail

readonly local_environment_file=".devcontainer/local.env"
if [[ -f "${local_environment_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${local_environment_file}"
    set +a
fi

npm_registry="${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org/}"
pypi_index="${PIP_INDEX_URL:-https://pypi.org/simple/}"
uv_default_index="${UV_DEFAULT_INDEX:-${pypi_index}}"

npm config set registry "${npm_registry}"
PIP_INDEX_URL="${pypi_index}" python -m pip install --user uv==0.9.17
export PATH="${HOME}/.local/bin:${PATH}"
UV_DEFAULT_INDEX="${uv_default_index}" uv sync