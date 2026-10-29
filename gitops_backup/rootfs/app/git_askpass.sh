#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# GIT_ASKPASS helper: hands git the GitHub token from the environment, so it
# is never written into /config/.git/config (and so never into HA backups).
case "$1" in
    Username*) echo "x-access-token" ;;
    *)         echo "${GITOPS_TOKEN:-}" ;;
esac
