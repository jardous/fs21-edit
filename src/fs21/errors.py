"""Errors that can be presented directly to users."""


class FS21Error(Exception):
    """Base class for expected application failures."""


class ProtocolError(FS21Error):
    """Traffic does not match the verified protocol."""


class CaptureError(FS21Error):
    """The capture is invalid, incomplete, or ambiguous."""


class TransportError(FS21Error):
    """USB access or communication failed."""


class UnsupportedOperation(FS21Error):
    """An operation is not established by available evidence."""


class EmptyBank(FS21Error):
    """The device explicitly reports zero audio blocks, not a timeout."""

    def __init__(self, bank: int) -> None:
        self.bank = bank
        super().__init__(f"Bank {bank:02d}: empty (explicit zero-block response).")


class UnsupportedRecording(UnsupportedOperation):
    """A recording requires an acknowledgment outside the captured range."""

    def __init__(self, bank: int, blocks: int, validated_blocks: int) -> None:
        self.bank, self.blocks = bank, blocks
        super().__init__(
            f"Bank {bank:02d}: unsupported ({blocks} blocks); only indices "
            f"0..{validated_blocks - 1} are capture-validated."
        )
