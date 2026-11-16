# SPDX-License-Identifier: Apache-2.0
"""``python -m ha_gitops.engine``: one run, selected by GITOPS_MODE."""

import sys

from ha_gitops.engine.runner import main

sys.exit(main())
