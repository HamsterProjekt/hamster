#!/usr/bin/env python3

# This file is part of Hamster
# Copyright (c) The Hamster time tracker developers
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hamster MCP server, on stdio.

Lets MCP clients (AI assistants) read and change the facts, activities
and categories, through the running hamster-service. See README.md.
"""

from hamster.mcpserver import main

if __name__ == '__main__':
    main()
