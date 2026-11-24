from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import usb.core

from fs21.audio import write_wav
from fs21.capture import Exchange
from fs21.device import FS21
from fs21.errors import ProtocolError, TransportError, UnsupportedOperation
from fs21.protocol import ALLOWED_COMMANDS, IDENTIFY, START_SLOT_1
from fs21.transport import USBTransport


def test_live_state_machine_against_capture(
    captured_exchanges: tuple[Exchange, ...], tmp_path: Path,
) -> None:
    transport = MagicMock()
    messages = [next(captured_exchanges[1].messages())]
    for exchange in captured_exchanges[4:47]:
        messages.extend(exchange.messages())
    transport.receive.side_effect = messages
    result = write_wav(FS21(transport).blocks(1), tmp_path / "live.wav")
    assert result.blocks == 42
    sent = [call.args[0] for call in transport.send.call_args_list]
    assert sent == [IDENTIFY] + [e.request.payload for e in captured_exchanges[4:47]]
    assert set(sent) <= ALLOWED_COMMANDS


def test_unknown_slot_sends_nothing() -> None:
    transport = MagicMock()
    with pytest.raises(UnsupportedOperation):
        list(FS21(transport).blocks(41))
    transport.send.assert_not_called()


def test_wrong_model_sends_no_start(captured_exchanges: tuple[Exchange, ...]) -> None:
    transport = MagicMock()
    message = next(captured_exchanges[1].messages())
    transport.receive.return_value = type(message)(
        message.body.replace(b"FLAMMA FS21", b"OTHER MODEL"), message.trailer
    )
    with pytest.raises(UnsupportedOperation):
        list(FS21(transport).blocks(1))
    transport.send.assert_called_once_with(IDENTIFY)


@pytest.mark.parametrize("failure", ["early_end", "wrong_order", "too_long"])
def test_device_refuses_bad_stream(
    failure: str, captured_exchanges: tuple[Exchange, ...],
) -> None:
    transport = MagicMock()
    messages = [next(captured_exchanges[1].messages()),
                next(captured_exchanges[4].messages())]
    if failure == "early_end":
        messages.append(next(captured_exchanges[46].messages()))
    elif failure == "wrong_order":
        messages.append(next(captured_exchanges[5].messages()))
    else:
        first = list(captured_exchanges[4].messages())[1]
        messages.append(type(first)(first.body[:2] + b"\xa3\0" + first.body[4:], first.trailer))
    transport.receive.side_effect = messages
    with pytest.raises((ProtocolError, UnsupportedOperation)):
        list(FS21(transport).blocks(1))
    assert [c.args[0] for c in transport.send.call_args_list] == [IDENTIFY, START_SLOT_1]


def mock_device(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    device = MagicMock()
    device.bus, device.address = 999, 99
    device.is_kernel_driver_active.return_value = False
    endpoints = [
        SimpleNamespace(bEndpointAddress=ep, bmAttributes=2, wMaxPacketSize=512)
        for ep in (1, 130)
    ]
    device.get_active_configuration.return_value.__getitem__.return_value = endpoints
    monkeypatch.setattr("usb.core.find", lambda **_: [device])
    monkeypatch.setattr("usb.util.claim_interface", MagicMock())
    monkeypatch.setattr("usb.util.release_interface", MagicMock())
    monkeypatch.setattr("usb.util.dispose_resources", MagicMock())
    return device


def test_transport_cleanup_and_no_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    with pytest.raises(RuntimeError), USBTransport():
        raise RuntimeError("interrupted")
    import usb.util
    usb.util.release_interface.assert_called_once_with(device, 0)
    usb.util.dispose_resources.assert_called_once_with(device)
    device.reset.assert_not_called()
    device.set_configuration.assert_not_called()
    device.detach_kernel_driver.assert_not_called()
    device.clear_halt.assert_not_called()


def test_unknown_command_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    with USBTransport() as transport, pytest.raises(UnsupportedOperation):
        transport.send(b"\xaa\x55" + b"\xff" * 510)
    device.write.assert_not_called()


def test_read_retry_never_replays_write(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    device.write.return_value = 512
    device.read.side_effect = [usb.core.USBTimeoutError("timeout"),
                              b"\xaa\x55\x02\0\0\0\xce\0\x02\0" + b"\0" * 502]
    with USBTransport(retries=1) as transport:
        transport.send(IDENTIFY)
        assert transport.receive().command == 0xCE
    assert device.read.call_count == 2
    assert device.write.call_count == 1


def test_ambiguous_write_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    device.write.side_effect = usb.core.USBTimeoutError("timeout")
    with USBTransport() as transport, pytest.raises(TransportError, match="not retried"):
        transport.send(IDENTIFY)
    assert device.write.call_count == 1


def test_short_read_is_not_silently_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    device.read.return_value = b"short"
    with USBTransport() as transport, pytest.raises(ProtocolError):
        transport.receive()
    assert device.read.call_count == 1


def test_kernel_driver_not_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    device.is_kernel_driver_active.return_value = True
    with pytest.raises(TransportError, match="refusing"):
        with USBTransport():
            pass
    device.detach_kernel_driver.assert_not_called()


def test_claim_failure_disposes(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    import usb.util
    usb.util.claim_interface.side_effect = usb.core.USBError("busy")
    with pytest.raises(TransportError):
        with USBTransport():
            pass
    usb.util.dispose_resources.assert_called_once_with(device)


def test_recovery_drains_stale_bytes_then_reidentifies(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.v3_replay import identity

    device = mock_device(monkeypatch)
    message = identity()
    wire = (b"\xaa\x55" + len(message.body).to_bytes(4, "little")
            + message.body + message.trailer).ljust(512, b"\0")
    device.read.side_effect = [b"stale audio".ljust(512, b"\0"),
                              usb.core.USBTimeoutError("quiet"), wire]
    device.write.return_value = 512
    with USBTransport() as transport:
        assert transport.drain_input() == 512
        assert FS21(transport).info()["model"] == "FLAMMA FS21"
    assert device.write.call_count == 1
    assert device.write.call_args.args[1] == IDENTIFY
    device.reset.assert_not_called()
    device.clear_halt.assert_not_called()


def test_recovery_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    device = mock_device(monkeypatch)
    device.read.return_value = bytes(512)
    with USBTransport() as transport, pytest.raises(TransportError, match="recovery budget"):
        transport.drain_input()
    assert device.read.call_count < 800
    device.write.assert_not_called()
    device.reset.assert_not_called()


def test_exhausted_timeout_does_not_publish_partial_wav(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from tests.v3_replay import identity

    device = mock_device(monkeypatch)
    def wire(body: bytes, trailer: bytes) -> bytes:
        return (b"\xaa\x55" + len(body).to_bytes(4, "little") + body + trailer).ljust(512, b"\0")
    message = identity()
    device.read.side_effect = [
        wire(message.body, message.trailer), wire(b"\xcb\0\x01\0\0", b"\x02\0"),
        usb.core.USBTimeoutError("timeout"), usb.core.USBTimeoutError("timeout"),
    ]
    device.write.return_value = 512
    with pytest.raises(TransportError), USBTransport(retries=1) as transport:
        write_wav(FS21(transport).blocks(1), tmp_path / "bad.wav")
    assert device.write.call_count == 2
    assert not list(tmp_path.iterdir())
    device.reset.assert_not_called()


def test_timeout_mid_frame_preserves_already_received_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fs21.protocol import decode_audio

    device = mock_device(monkeypatch)
    body = b"\xcc\0\x01\0\0\0" + bytes(600)
    wire = (b"\xaa\x55" + len(body).to_bytes(4, "little") + body + bytes(2)).ljust(1024, b"\0")
    device.read.side_effect = [wire[:512], usb.core.USBTimeoutError("transient"), wire[512:]]
    with USBTransport(retries=1) as transport:
        assert decode_audio(transport.receive()).pcm == bytes(600)
    assert device.read.call_count == 3
    device.write.assert_not_called()


def test_bank8_capture_replay_through_pyusb_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.v3_replay import CaptureReplay

    device = mock_device(monkeypatch)
    replay = CaptureReplay(8)
    pending = b""
    position = 0
    transfers = 0

    def write(endpoint: int, command: bytes, timeout: int) -> int:
        assert endpoint == 1 and timeout > 0
        replay.send(command)
        return len(command)

    def read(endpoint: int, size: int, timeout: int) -> bytes:
        nonlocal pending, position, transfers
        assert endpoint == 0x82 and size == 512 and timeout > 0
        if not pending:
            message = replay.receive()
            logical = (b"\xaa\x55" + len(message.body).to_bytes(4, "little")
                       + message.body + message.trailer)
            pending = logical.ljust((len(logical) + 511) // 512 * 512, b"\0")
        packet = pending[position : position + 512]
        position += 512
        if position == len(pending):
            pending, position = b"", 0
        transfers += 1
        return packet

    device.read = read
    device.write = write
    with USBTransport() as transport:
        size = sum(len(block.pcm) for block in FS21(transport).blocks(8))
    assert size == 63505920
    assert transfers == 124200  # ID + CB + CC padded transfers + CE.
    assert not pending and not replay.expected and not replay.pending
    device.reset.assert_not_called()
