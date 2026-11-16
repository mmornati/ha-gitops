# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Anil Belur
ARG BUILD_FROM
FROM ${BUILD_FROM}

# git does the versioning, inotify-tools watches /config, Python runs the app.
RUN apk upgrade --no-cache && apk add --no-cache git inotify-tools python3

# Runtime libraries at the versions Home Assistant itself pins, checked against
# the hashes in requirements.txt. Wheels only: nothing is compiled or run from
# a source archive. pip is removed again once they are installed.
COPY requirements.txt /tmp/requirements.txt
RUN apk add --no-cache --virtual .install py3-pip \
    && pip install --no-cache-dir --break-system-packages --require-hashes \
        --only-binary=:all: -r /tmp/requirements.txt \
    && apk del --no-cache .install \
    && rm /tmp/requirements.txt

COPY rootfs /
RUN chmod a+x /app/ha_gitops/git_askpass.sh /etc/services.d/gitops/run \
    && python3 -m compileall -q /app

LABEL \
  io.hass.type="addon" \
  org.opencontainers.image.source="https://github.com/mmornati/ha-gitops" \
  org.opencontainers.image.licenses="Apache-2.0"
