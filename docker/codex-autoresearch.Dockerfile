FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CONDA_DIR=/opt/conda
ENV CODEX_HOME=/home/codex/.codex
ENV MPLCONFIGDIR=/workspace/tmp/mplconfig
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/conda/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    libgomp1 \
    nodejs \
    npm \
    tini \
    && rm -rf /var/lib/apt/lists/*

RUN arch="$(dpkg --print-architecture)" && \
    case "$arch" in \
      amd64) miniforge_arch="x86_64" ;; \
      arm64) miniforge_arch="aarch64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac && \
    curl -fsSL -o /tmp/miniforge.sh \
      "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-${miniforge_arch}.sh" && \
    bash /tmp/miniforge.sh -b -p "${CONDA_DIR}" && \
    rm -f /tmp/miniforge.sh

RUN conda create -y -n qlib python=3.10 && \
    conda run -n qlib python -m pip install --no-cache-dir --upgrade pip && \
    conda run -n qlib python -m pip install --no-cache-dir \
      lightgbm \
      matplotlib \
      numpy \
      pandas \
      pyqlib && \
    conda clean -afy

RUN npm i -g @openai/codex@latest && npm cache clean --force

RUN useradd -m -s /bin/bash codex && \
    mkdir -p /workspace /home/codex/.codex && \
    chown -R codex:codex /workspace /home/codex

WORKDIR /workspace
USER codex

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
