FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY sql ./sql
ENTRYPOINT ["cnpj-etl"]
CMD ["run"]

