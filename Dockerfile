FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

ARG FACEFUSION_VERSION=3.8.2

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PYTHONUNBUFFERED=1

WORKDIR /facefusion

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        git \
        pip \
        python-is-python3 \
        python3.12 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch "${FACEFUSION_VERSION}" --depth 1 \
      https://github.com/facefusion/facefusion.git . \
    && python install.py cuda@12 --skip-conda \
    && pip install --no-cache-dir "runpod==1.7.13" "requests==2.32.5"

# Bake the lite model set into the image so workers do not download weights per cold start.
RUN python facefusion.py force-download --download-scope lite

COPY handler.py /handler.py

CMD ["python", "-u", "/handler.py"]
