"""Minimal RFC 6455 client sufficient for the short eWeLink probe session."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from typing import Any


class WebSocketError(RuntimeError):
    pass


def encode_client_frame(payload: bytes, opcode: int = 1, mask: bytes | None = None) -> bytes:
    mask = os.urandom(4) if mask is None else mask
    if len(mask) != 4:
        raise ValueError("WebSocket mask must be four bytes")
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return bytes(header) + mask + masked


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise WebSocketError("WebSocket closed unexpectedly")
        data.extend(chunk)
    return bytes(data)


class WebSocket:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    @classmethod
    def connect(cls, host: str, port: int, path: str = "/api/ws", timeout: float = 10.0) -> "WebSocket":
        raw = socket.create_connection((host, port), timeout=timeout)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw, server_hostname=host)
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response and len(response) < 64 * 1024:
            response.extend(_recv_exact(sock, 1))
        lines = bytes(response).split(b"\r\n")
        if not lines or b" 101 " not in lines[0]:
            sock.close()
            raise WebSocketError("WebSocket upgrade was rejected")
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                name, value = line.split(b":", 1)
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if headers.get(b"sec-websocket-accept") != expected:
            sock.close()
            raise WebSocketError("Invalid WebSocket accept value")
        return cls(sock)

    def send_json(self, value: Any) -> None:
        self.sock.sendall(encode_client_frame(json.dumps(value).encode("utf-8")))

    def _send_control(self, opcode: int, payload: bytes) -> None:
        self.sock.sendall(encode_client_frame(payload, opcode=opcode))

    def receive_json(self, timeout: float | None = None) -> dict[str, Any]:
        if timeout is not None:
            self.sock.settimeout(timeout)
        while True:
            first, second = _recv_exact(self.sock, 2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", _recv_exact(self.sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _recv_exact(self.sock, 8))[0]
            mask = _recv_exact(self.sock, 4) if masked else None
            payload = _recv_exact(self.sock, length)
            if mask:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 9:  # ping
                self._send_control(10, payload)
                continue
            if opcode == 8:
                raise WebSocketError("WebSocket closed by server")
            if opcode != 1:
                continue
            return json.loads(payload)

    def close(self) -> None:
        try:
            self._send_control(8, b"")
        except OSError:
            pass
        self.sock.close()

