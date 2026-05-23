FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        findutils \
        gawk \
        git \
        grep \
        jq \
        build-essential \
        pkg-config \
        python3 \
        python3-dev \
        python3-venv \
        python3-pytest \
        ripgrep \
        sed \
        tar \
        unzip \
    && npm install -g pnpm \
    && mkdir -p /workspace \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/local/bin/python
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /workspace

CMD ["sleep", "infinity"]
