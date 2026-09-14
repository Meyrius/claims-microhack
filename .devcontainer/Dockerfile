FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# Keep the package manager independent of PyPI mirrors that may not carry uv itself.
COPY --from=ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 /uv /uvx /bin/

# Remove Yarn apt repo entirely to avoid expired GPG key errors during feature installs
USER root
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && rm -f /usr/share/keyrings/yarn.gpg
