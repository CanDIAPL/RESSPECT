FROM python:3.12-slim AS builder

# Use the FASTDB client from the main branch by default. Override this argument
# to build with a different branch.
ARG FASTDB_CLIENT_URL=https://raw.githubusercontent.com/LSSTDESC/FASTDB/main/client/fastdb_client.py
ARG RESSPECT_VERSION=0.0.0

WORKDIR /build/resspect
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY data/tests/test_features.csv ./data/tests/test_features.csv

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --timeout 120 --retries 10 \
       'astropy>=5.2.1' \
       'iminuit>=1.20.0' \
       'light_curve>=0.8.1' \
       'matplotlib>=3.7.0' \
       'numpy>=1.24.2' \
       'pandas>=1.5.3' \
       'pooch>=1.8' \
       'progressbar2>=4.2.0' \
       'pycryptodome>=3.20' \
       'PyYAML>=6.0' \
       'requests>=2.31' \
       'scikit-learn>=1.2.1' \
       'seaborn>=0.12.2' \
       'scipy>=1.10.0' \
    && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RESSPECT="${RESSPECT_VERSION}" \
       /opt/venv/bin/pip install --no-cache-dir --timeout 120 --retries 10 --no-deps . \
    && mkdir -p /opt/fastdb-client \
    && python -c "import urllib.request; urllib.request.urlretrieve( \
       '${FASTDB_CLIENT_URL}', \
       '/opt/fastdb-client/fastdb_client.py')"

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 resspect

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/fastdb-client /opt/fastdb-client
COPY --from=builder /build/resspect/data/tests/test_features.csv \
    /opt/resspect/bootstrap/test_features.csv

ENV HOME=/home/resspect \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

USER 10001:10001
WORKDIR /home/resspect
ENTRYPOINT ["resspect_daily"]
