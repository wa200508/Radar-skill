# syntax=docker/dockerfile:1
FROM python:3.12.14-slim-trixie
WORKDIR /opt/radar-skill
COPY --chmod=755 requirements-lock.txt ./
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir -r requirements-lock.txt; \
    groupadd --gid 10001 rf; useradd --uid 10001 --gid rf --create-home rf
COPY --chmod=755 . .
RUN python -m pip --disable-pip-version-check install --no-cache-dir --no-deps --no-build-isolation .
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/wa200508/Radar-Skill" \
      org.opencontainers.image.title="Radar RF Skill" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision=${SOURCE_REVISION}
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER rf
EXPOSE 8811 8812
HEALTHCHECK --interval=5s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:'+os.environ.get('RF_HTTP_PORT','8812')+'/health',timeout=2)"
ENTRYPOINT ["python", "-m", "radar_skill"]
CMD ["--arrays", "/config/arrays.json", "--results-dir", "/results"]
