"""
Mock RCON Server for testing bridge.py without a real Minecraft server.

Implements the RCON protocol at the socket level (same protocol used by mcrcon):
  - 4-byte length prefix (little-endian int32)
  - 4-byte request ID (little-endian int32)
  - 4-byte type (little-endian int32)
  - Payload (null-terminated string)
  - 2-byte padding (little-endian int16, usually 0x0000)

Packet types:
  - 3 = LOGIN
  - 2 = COMMAND
  - 0 = RESPONSE
  - 2 = COMMAND_RESPONSE (type value 0)

Usage:
    server = MockRCONServer(port=25576)
    server.start()
    server.set_response("say hello", "Hello back!")
    # ... test code connects to localhost:25576 ...
    server.stop()

    # Or as a context manager:
    with MockRCONServer(port=25576) as server:
        ...
"""

import socket
import struct
import threading
from typing import Dict, Optional, List, Callable
import re


# RCON packet types
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

# Padding after RCON payload
PADDING = struct.pack("<h", 0)


def _encode_packet(req_id: int, pkt_type: int, payload: str) -> bytes:
    """Encode an RCON packet."""
    body = struct.pack("<ii", req_id, pkt_type) + payload.encode("utf-8") + b"\x00" + PADDING
    length = len(body)
    return struct.pack("<i", length) + body


def _decode_packet(data: bytes):
    """Decode an RCON packet. Returns (req_id, pkt_type, payload)."""
    if len(data) < 14:
        raise ValueError("Packet too short")
    length = struct.unpack_from("<i", data, 0)[0]
    if len(data) < 4 + length:
        raise ValueError("Incomplete packet")
    req_id = struct.unpack_from("<i", data, 4)[0]
    pkt_type = struct.unpack_from("<i", data, 8)[0]
    payload = data[12:12 + length - 10].decode("utf-8", errors="replace").rstrip("\x00")
    return req_id, pkt_type, payload


class MockRCONServer:
    """Fake RCON server for testing bridge.py without a real MC server."""

    def __init__(self, host: str = "localhost", port: int = 25576, password: str = "mira"):
        self.host = host
        self.port = port
        self.password = password

        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Command response patterns: (pattern_str, response) pairs
        self._responses: List[Dict[str, str]] = []

        # All received commands (for assertions)
        self.received_commands: List[str] = []

        # Lock for thread safety
        self._lock = threading.Lock()

    def start(self):
        """Start the mock server in a background thread."""
        if self._running:
            return self

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.listen(5)
        self._socket.settimeout(1.0)

        self._running = True
        self.received_commands = []

        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Stop the mock server."""
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def set_response(self, command_pattern: str, response: str):
        """Set a response for commands matching the given pattern.

        Pattern matching uses substring match (case-sensitive).
        The first matching pattern wins when responding.

        Args:
            command_pattern: Substring to match against received commands
            response: Response string to return
        """
        with self._lock:
            # Insert at front so most recently added patterns match first
            self._responses.insert(0, {"pattern": command_pattern, "response": response})

    def clear_responses(self):
        """Clear all configured responses."""
        with self._lock:
            self._responses.clear()

    def get_received_commands(self) -> List[str]:
        """Return a copy of all commands received so far."""
        with self._lock:
            return list(self.received_commands)

    def _handle_client(self, conn: socket.socket):
        """Handle a single client connection."""
        try:
            conn.settimeout(5.0)

            # Read login packet
            data = self._recv_all(conn)
            if not data:
                return

            req_id, pkt_type, payload = _decode_packet(data)

            # Must be auth packet
            if pkt_type != SERVERDATA_AUTH:
                return

            # Verify password
            if payload == self.password:
                auth_id = req_id  # positive = success
            else:
                auth_id = -1  # negative = failure

            # Send auth response
            conn.sendall(_encode_packet(auth_id, SERVERDATA_AUTH_RESPONSE, ""))

            # Main command loop
            while self._running:
                data = self._recv_all(conn)
                if not data:
                    break

                try:
                    req_id, pkt_type, payload = _decode_packet(data)
                except (ValueError, struct.error):
                    break

                if pkt_type != SERVERDATA_EXECCOMMAND:
                    continue

                # Record the command
                with self._lock:
                    self.received_commands.append(payload)

                # Find matching response
                response = self._find_response(payload)

                # Send response (must use the same req_id as the command)
                conn.sendall(_encode_packet(req_id, SERVERDATA_RESPONSE_VALUE, response))

                # Also send an empty response to mark end (mcrcon expects this)
                conn.sendall(_encode_packet(req_id, SERVERDATA_RESPONSE_VALUE, ""))

        except (socket.timeout, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _find_response(self, command: str) -> str:
        """Find a response for the given command using configured patterns."""
        with self._lock:
            for entry in self._responses:
                pattern = entry["pattern"]
                if pattern in command:
                    return entry["response"]
        return ""

    def _recv_all(self, conn: socket.socket) -> Optional[bytes]:
        """Receive a complete RCON packet."""
        # Read 4-byte length
        header = b""
        while len(header) < 4:
            try:
                chunk = conn.recv(4 - len(header))
                if not chunk:
                    return None
                header += chunk
            except socket.timeout:
                continue
            except OSError:
                return None

        length = struct.unpack_from("<i", header, 0)[0]
        if length < 0 or length > 65536:
            return None

        # Read the rest of the packet
        data = header
        while len(data) < 4 + length:
            try:
                chunk = conn.recv(4 + length - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                continue
            except OSError:
                return None

        return data

    def _serve(self):
        """Main server loop accepting connections."""
        while self._running:
            try:
                conn, addr = self._socket.accept()
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn,),
                    daemon=True,
                )
                client_thread.start()
            except socket.timeout:
                continue
            except OSError:
                break
