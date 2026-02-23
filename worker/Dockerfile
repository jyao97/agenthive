FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools
RUN apt-get update && apt-get install -y \
    git curl wget ca-certificates \
    python3 python3-pip python3-venv \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Git config (worker level)
RUN git config --global user.name "CC Worker" && \
    git config --global user.email "cc-worker@localhost" && \
    git config --global init.defaultBranch main

# No SSH client installed
# No keys placed
# No sudo

# Non-root user
RUN useradd -m -s /bin/bash ccworker
USER ccworker

WORKDIR /projects

COPY --chown=ccworker:ccworker entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
