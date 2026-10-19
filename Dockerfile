# Explicit synthetic demonstration only; never configure production inference here.
FROM python:3.12-slim AS demo-provider
RUN useradd --create-home --uid 10001 foundry
COPY scripts/demo.py /demo.py
USER foundry
ENTRYPOINT ["python", "/demo.py", "--serve-provider"]

# Each public service is an independently installable wheel and runtime target.
FROM python:3.12-slim AS wheels
RUN pip install --no-cache-dir uv==0.11.4
WORKDIR /source
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv build --all-packages --out-dir /wheels
RUN uv export --frozen --all-packages --all-extras --no-dev --no-emit-workspace --no-hashes --output-file /constraints.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 foundry && mkdir /data && chown foundry:foundry /data
COPY --from=wheels /wheels /wheels
COPY --from=wheels /constraints.txt /constraints.txt
WORKDIR /data

FROM runtime AS warehouse
RUN pip install --no-cache-dir --constraint /constraints.txt --find-links /wheels packet-foundry-warehouse==0.1.2 && rm -rf /wheels
USER foundry
ENTRYPOINT ["foundry-warehouse"]
CMD ["run", "--host", "0.0.0.0", "--port", "8101"]

FROM runtime AS registry
RUN pip install --no-cache-dir --constraint /constraints.txt --find-links /wheels packet-foundry-registry==0.1.2 && rm -rf /wheels
USER foundry
ENTRYPOINT ["foundry-registry"]
CMD ["run"]

FROM runtime AS collector
RUN pip install --no-cache-dir --constraint /constraints.txt --find-links /wheels packet-foundry-collector==0.1.2 && rm -rf /wheels
USER foundry
ENTRYPOINT ["foundry-collector"]
CMD ["run", "--config", "/config/collector.json"]

FROM runtime AS factory
RUN pip install --no-cache-dir --constraint /constraints.txt --find-links /wheels 'packet-foundry-factory[tracing]==0.1.2' && rm -rf /wheels
USER foundry
ENTRYPOINT ["foundry-factory"]
CMD ["run"]
