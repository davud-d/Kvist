# -*- coding: utf-8 -*-
"""Public-only, DNS-pinned transport for user-supplied model endpoints."""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx


class PublicEndpointTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.raw_host.decode("ascii")
        if request.url.scheme != "https" or request.url.userinfo:
            raise httpx.ConnectError("Only public HTTPS endpoints are allowed", request=request)
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, request.url.port or 443, type=socket.SOCK_STREAM), 5.0)
        except (OSError, TimeoutError) as error:
            raise httpx.ConnectError("Endpoint DNS lookup failed", request=request) from error
        addresses = [ipaddress.ip_address(record[4][0]) for record in records]
        if not addresses or any(not address.is_global or address.is_multicast for address in addresses):
            raise httpx.ConnectError("Endpoint resolves to a non-public address", request=request)
        # Connect to the inspected address, preserving Host and TLS verification for the original hostname.
        # Resolving the hostname a second time would reintroduce DNS rebinding.
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=str(addresses[0])), headers=request.headers,
            stream=request.stream, extensions={**request.extensions, "sni_hostname": host},
        )
        return await super().handle_async_request(pinned)
