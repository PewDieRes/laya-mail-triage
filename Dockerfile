FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/root/.cache/huggingface

# CPU-only torch first so laya does not pull the CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements-base.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY triage ./triage
COPY scripts ./scripts

CMD ["python", "-m", "triage.main", "run"]
