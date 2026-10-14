"""Local control channel so the widget (or a second launch) can drive the running app.

Newline-delimited JSON over a named pipe (\\\\.\\pipe\\DeskControl on Windows), restricted to the current user.
Requests: {"cmd": "state" | "show" | "quit" | "stop" | "preset", "index": n | "move", "height_cm": x}
"""
import json
from typing import Callable, Optional

from PySide6.QtNetwork import QLocalServer, QLocalSocket

PIPE_NAME = "DeskControl"


class ControlServer:
    def __init__(self, handler: Callable[[dict], dict]):
        self.handler = handler
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._accept)

    def listen(self) -> bool:
        return self.server.listen(PIPE_NAME)

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()
            socket.readyRead.connect(lambda s=socket, b=buffer: self._read(s, b))
            socket.disconnected.connect(socket.deleteLater)
            self._read(socket, buffer)

    def _read(self, socket: QLocalSocket, buffer: bytearray) -> None:
        buffer.extend(bytes(socket.readAll()))
        while b"\n" in buffer:
            line, _, rest = bytes(buffer).partition(b"\n")
            buffer[:] = rest
            try:
                reply = self.handler(json.loads(line))
            except Exception as e:
                reply = {"ok": False, "error": str(e) or type(e).__name__}
            socket.write(json.dumps(reply).encode() + b"\n")
            socket.flush()


def send(request: dict, timeout_ms: int = 500) -> Optional[dict]:
    """Send one request to a running instance; None if there isn't one."""
    socket = QLocalSocket()
    socket.connectToServer(PIPE_NAME)
    if not socket.waitForConnected(timeout_ms):
        return None
    socket.write(json.dumps(request).encode() + b"\n")
    socket.waitForBytesWritten(timeout_ms)
    data = b""
    while b"\n" not in data and socket.waitForReadyRead(timeout_ms):
        data += bytes(socket.readAll())
    socket.disconnectFromServer()
    return json.loads(data) if data else {}
