# -*- coding: utf-8 -*-
"""A custom model URL must not turn the control server into an internal-network proxy."""
from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from kvist_cloud.outbound import PublicEndpointTransport


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1",
                                    "::ffff:127.0.0.1", "100.64.0.1", "224.0.0.1"])
async def test_private_dns_answers_are_blocked(monkeypatch, address):
    async def lookup(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    async with httpx.AsyncClient(transport=PublicEndpointTransport()) as client:
        with pytest.raises(httpx.ConnectError, match="non-public"):
            await client.get("https://attacker.example/models")


async def test_connects_to_checked_ip_with_original_host_and_sni(monkeypatch):
    calls = []
    async def lookup(host, port, **kwargs):
        calls.append(host)
        assert len(calls) == 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
    async def send(self, request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "model.example"
        assert request.extensions["sni_hostname"] == "model.example"
        return httpx.Response(200, json={"data": []})
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    async with httpx.AsyncClient(transport=PublicEndpointTransport()) as client:
        assert (await client.get("https://model.example/models")).status_code == 200
    assert calls == ["model.example"]
