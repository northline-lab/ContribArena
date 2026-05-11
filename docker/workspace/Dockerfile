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
        python3 \
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

WORKDIR /workspace

CMD ["sleep", "infinity"]
