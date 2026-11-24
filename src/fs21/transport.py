"""PyUSB transport: claim only an already configured device, never reset it."""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import tempfile
from collections.abc import Iterator
from types import TracebackType
from typing import Any, BinaryIO, Self

import usb.core
import usb.util

from fs21.errors import ProtocolError, TransportError, UnsupportedOperation
from fs21.protocol import (
    ALLOWED_COMMANDS,
    BOOT_PID,
    EP_IN,
    EP_OUT,
    MAX_BODY,
    PACKET_SIZE,
    PID,
    VID,
    Message,
    decode_message,
    wire_size,
)

log = logging.getLogger(__name__)


class USBTransport:
    def __init__(
        self, timeout_ms: int = 3000, retries: int = 2,
        address: tuple[int, int] | None = None,
    ) -> None:
        if timeout_ms <= 0 or not 0 <= retries <= 10:
            raise ValueError("Timeout must be positive and retries must be between 0 and 10.")
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.address = address
        self.device: Any = None
        self._claimed = False
        self._lock: BinaryIO | None = None

    def __enter__(self) -> Self:
        try:
            devices = list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID))
            if self.address:
                devices = [d for d in devices if (d.bus, d.address) == self.address]
            if not devices:
                if usb.core.find(idVendor=VID, idProduct=BOOT_PID) is not None:
                    raise TransportError("Pedal is in firmware/HID mode; no commands will be sent.")
                raise TransportError("FS21 0483:1412 not found. Connect it in normal USB mode.")
            if len(devices) != 1:
                raise TransportError("Multiple FS21 devices found; use --usb-address BUS:DEVICE.")
            self.device = devices[0]
            lock_path = os.path.join(
                tempfile.gettempdir(),
                f"fs21-{os.getuid()}-{self.device.bus}-{self.device.address}.lock",
            )
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            self._lock = os.fdopen(fd, "a+b")
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TransportError("Another fs21 process is already using this pedal.") from exc
            config = self.device.get_active_configuration()
            interface = config[(0, 0)]
            endpoints = {e.bEndpointAddress: e for e in interface}
            for address in (EP_IN, EP_OUT):
                endpoint = endpoints.get(address)
                if (endpoint is None or endpoint.bmAttributes & 3 != 2
                        or endpoint.wMaxPacketSize != PACKET_SIZE):
                    raise TransportError(
                        "USB interface does not match the captured bulk endpoints."
                    )
            if self.device.is_kernel_driver_active(0):
                raise TransportError(
                    "Interface 0 is in use by a kernel driver; refusing to detach it."
                )
            usb.util.claim_interface(self.device, 0)
            self._claimed = True
            log.info("Claimed FS21 bus=%s device=%s", self.device.bus, self.device.address)
            return self
        except BaseException as exc:
            self.close()
            if isinstance(exc, usb.core.USBError):
                raise TransportError(self._explain(exc)) from exc
            raise

    @staticmethod
    def _explain(exc: Exception) -> str:
        return f"USB error: {exc}. Check permissions (packaging/70-fs21.rules) and other editors."

    def close(self) -> None:
        try:
            if self.device is not None:
                try:
                    if self._claimed:
                        usb.util.release_interface(self.device, 0)
                finally:
                    usb.util.dispose_resources(self.device)
        finally:
            self._claimed = False
            self.device = None
            if self._lock:
                self._lock.close()
                self._lock = None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def send(self, command: bytes) -> None:
        if command not in ALLOWED_COMMANDS:
            raise UnsupportedOperation("USB command is not in the finite read-only allowlist.")
        if self.device is None or not self._claimed:
            raise TransportError("USB interface is not claimed.")
        log.debug("OUT %s", command[:12].hex(" "))
        try:
            sent = self.device.write(EP_OUT, command, timeout=self.timeout_ms)
        except usb.core.USBError as exc:
            # An ambiguous write may already have reached the device. Never replay it.
            raise TransportError(f"{self._explain(exc)} Command was not retried.") from exc
        if sent != len(command):
            raise TransportError("Short USB command write; refusing to retry an ambiguous command.")

    def _read_packet(self) -> bytes:
        if self.device is None or not self._claimed:
            raise TransportError("USB interface is not claimed.")
        for attempt in range(self.retries + 1):
            try:
                data = bytes(self.device.read(EP_IN, PACKET_SIZE, timeout=self.timeout_ms))
                if len(data) != PACKET_SIZE:
                    raise ProtocolError(f"Expected 512-byte IN transfer, received {len(data)}.")
                return data
            except usb.core.USBError as exc:
                transient = isinstance(exc, usb.core.USBTimeoutError) or exc.errno == errno.EINTR
                if not transient or attempt == self.retries:
                    raise TransportError(self._explain(exc)) from exc
                log.warning("Transient USB read error (%s), retry %d/%d",
                            exc, attempt + 1, self.retries)
        raise AssertionError("Unreachable")

    def receive(self) -> Message:
        data = bytearray(self._read_packet())
        expected = wire_size(bytes(data))
        while len(data) < expected:
            data.extend(self._read_packet())
        message = decode_message(bytes(data))
        log.debug("IN command=%02x body=%d trailer=%s",
                  message.command, len(message.body), message.trailer.hex())
        return message

    def drain_input(self) -> int:
        """Discard stale IN bytes after a failed bank, without sending any command.

        Stop-and-wait permits at most one audio response plus a small reply to
        be queued. A bounded read-to-quiet is safer than resetting USB or
        replaying a timed-out OUT. New identification must still validate.
        """
        if self.device is None or not self._claimed:
            raise TransportError("USB interface is not claimed.")
        maximum_packets = (MAX_BODY + 8 + PACKET_SIZE - 1) // PACKET_SIZE + 2
        drained = 0
        for _ in range(maximum_packets + 1):
            try:
                packet = bytes(self.device.read(
                    EP_IN, PACKET_SIZE, timeout=min(self.timeout_ms, 200)
                ))
                drained += len(packet)
            except usb.core.USBTimeoutError:
                log.info("usb_input_recovered", extra={"drained_bytes": drained})
                return drained
            except usb.core.USBError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise TransportError(self._explain(exc)) from exc
        raise TransportError("USB input did not become quiet within the recovery budget.")

    def descriptor_info(self) -> dict[str, object]:
        if self.device is None:
            raise TransportError("USB interface is not claimed.")
        return {
            "vid": f"{VID:04x}", "pid": f"{PID:04x}",
            "bus": self.device.bus, "address": self.device.address,
            "manufacturer": self.device.manufacturer, "product": self.device.product,
            "serial": self.device.serial_number,
            "interface": 0, "out_endpoint": "0x01", "in_endpoint": "0x82",
            "max_packet_size": PACKET_SIZE,
        }


def connected_addresses() -> Iterator[tuple[int, int]]:
    """Descriptor enumeration only; no vendor commands and no interface claim."""
    for device in usb.core.find(find_all=True, idVendor=VID, idProduct=PID):
        yield device.bus, device.address
