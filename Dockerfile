FROM python:3.13-slim

WORKDIR /app
ENV TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY README.md ./
COPY src/ src/
COPY profiles/ profiles/
COPY scripts/ scripts/

RUN pip install --no-cache-dir -e ".[web]" fastapi "uvicorn[standard]" python-dotenv

RUN mkdir -p /app/data && useradd -m agent
USER agent

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "agent.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
