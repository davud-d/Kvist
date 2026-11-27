# -*- coding: utf-8 -*-
"""An MCP server that lets Kvist work in the person's own running browser.

The browser is reached through the debugging port it opened itself: Chromium
family browsers speak the Chrome DevTools Protocol, Firefox speaks WebDriver
BiDi (it dropped CDP). Input goes through the protocol's input domain, so the
page receives ordinary trusted mouse and keyboard events; the on-page overlay
(glowing frame, "Kvist is working here" pill with Stop, blue cursor) lives in an
isolated script world the page's own scripts cannot see.

Started as ``kvist --browser-mcp [--browser-endpoint 9222]``; standard library
only, so it also runs from a frozen build.
"""
from __future__ import annotations
