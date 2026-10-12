# -*- coding: utf-8 -*-
"""Installed skills: discovery, approval, isolated execution and the core API they call.

The package is split by trust: ``manifest``/``registry``/``grants`` read
packages without running them, ``host``/``services`` are the core side of the
runner protocol, and ``runner/`` is the stdlib-only code mounted read-only
into the sandbox. ``runner/`` never imports ``kernel``.
"""
