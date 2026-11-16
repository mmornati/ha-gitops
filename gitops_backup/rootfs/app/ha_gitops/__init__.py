# SPDX-License-Identifier: Apache-2.0
"""HA GitOps: PR-gated GitHub sync for a Home Assistant configuration.

The package is layered, and a layer only imports from the ones above it:

* ``rules``      pure decisions, no I/O (what to ignore, what a change needs,
                 what a PR body says, what looks like a secret)
* ``adapters``   the outside world: git, GitHub, Home Assistant, files on disk
* ``engine``     one run against /config (sync, backup, revert, discard);
                 ``python -m ha_gitops.engine``
* ``controller`` the long-running service that decides when the engine runs;
                 ``python -m ha_gitops.controller``
"""
