ARG BUILD_FROM=python:3.13-slim
FROM ${BUILD_FROM}

# The add-on runs the code from this repository at the tag for its version (agent-v<version>); CI overrides
# SOURCE_REF with the commit it's testing.
ARG BUILD_VERSION=1.0.0
ARG SOURCE_REF=agent-v${BUILD_VERSION}
ADD https://github.com/steves165/desk-control/archive/${SOURCE_REF}.tar.gz /tmp/source.tar.gz
RUN mkdir /app \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app \
    && rm /tmp/source.tar.gz \
    && pip install --no-cache-dir -r /app/requirements-agent.txt

ENV PYTHONUNBUFFERED=1
WORKDIR /app
CMD ["python", "agent.py", "--data", "/config"]
