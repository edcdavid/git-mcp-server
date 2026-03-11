FROM python:3.12-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    mcp-server-git \
    "mcp[cli]>=1.8.0" \
    uvicorn \
    starlette

WORKDIR /app
COPY server.py .

RUN mkdir -p /repos

EXPOSE 8080

ENTRYPOINT ["python", "server.py"]
