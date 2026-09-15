#!/usr/bin/env bash
set -euo pipefail

readonly script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly repository_root="$(cd -- "${script_directory}/.." && pwd)"
readonly local_environment_file="${script_directory}/local.env"

cd "${repository_root}"

if [[ -f "${local_environment_file}" ]]; then
    echo "Using local package and proxy settings from .devcontainer/local.env."
    set -a
    # shellcheck disable=SC1090
    source <(sed 's/\r$//' "${local_environment_file}")
    set +a
else
    echo "No .devcontainer/local.env found; using public package registries."
fi

npm_registry="${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org/}"
uv_default_index="${UV_DEFAULT_INDEX:-${PIP_INDEX_URL:-https://pypi.org/simple/}}"
uv_link_mode="${UV_LINK_MODE:-copy}"

npm config set registry "${npm_registry}"
if ! UV_DEFAULT_INDEX="${uv_default_index}" UV_LINK_MODE="${uv_link_mode}" uv sync; then
    echo >&2
    echo "Unable to resolve the Python environment from the configured uv index." >&2
    echo "Check .devcontainer/local.env and your corporate proxy or CA configuration." >&2
    exit 1
fi

UV_DEFAULT_INDEX="${uv_default_index}" UV_LINK_MODE="${uv_link_mode}" \
    uv venv --seed --allow-existing .venv
