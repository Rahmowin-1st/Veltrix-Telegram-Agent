"""All QA is offline: external socket connections are prohibited in tests."""

import ipaddress
import socket

import pytest


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def is_local(address):
        if not isinstance(address, tuple):
            return True  # Local pipes/UNIX sockets used by the test runner.
        try:
            return ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            return address[0] == "localhost"

    def connect(sock, address):
        if not is_local(address):
            raise RuntimeError("External network disabled during offline tests")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not is_local(address):
            raise RuntimeError("External network disabled during offline tests")
        return original_connect_ex(sock, address)

    def resolve(host, *args, **kwargs):
        if host not in {None, "localhost", "127.0.0.1", "::1", b"localhost"}:
            raise RuntimeError("External DNS disabled during offline tests")
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
