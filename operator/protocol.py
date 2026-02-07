"""
Binary communication protocol (see docs/communication_protocol.md).
Frame: header 0xAADD | length (1 byte) | contents (0-255 bytes) | checksum (1 byte).
Checksum = sum of content bytes, low 8 bits only.
"""

HEADER = bytes((0xAA, 0xDD))
HEADER_LEN = 2
LENGTH_LEN = 1
CHECKSUM_LEN = 1
MIN_FRAME_LEN = HEADER_LEN + LENGTH_LEN + CHECKSUM_LEN  # 4
MAX_CONTENT_LEN = 255


def checksum(contents: bytes) -> int:
    """Checksum over contents only; return low 8 bits."""
    return sum(contents) & 0xFF


def encode(contents: bytes) -> bytes:
    """Build a full frame: header + length + contents + checksum."""
    if len(contents) > MAX_CONTENT_LEN:
        raise ValueError(f"contents length {len(contents)} > {MAX_CONTENT_LEN}")
    length_byte = len(contents).to_bytes(1, "big")
    cs = checksum(contents)
    return HEADER + length_byte + contents + bytes((cs,))


def decode_frame(data: bytes) -> tuple[bytes, int] | None:
    """
    Parse one complete frame from the start of data.
    Returns (contents, num_bytes_consumed) or None if invalid/insufficient.
    """
    if len(data) < MIN_FRAME_LEN:
        return None
    if data[:HEADER_LEN] != HEADER:
        return None
    length = data[HEADER_LEN]
    if length > MAX_CONTENT_LEN:
        return None
    frame_len = HEADER_LEN + LENGTH_LEN + length + CHECKSUM_LEN
    if len(data) < frame_len:
        return None
    payload = data[HEADER_LEN + LENGTH_LEN : HEADER_LEN + LENGTH_LEN + length]
    received_cs = data[frame_len - 1]
    if checksum(payload) != received_cs:
        return None
    return (payload, frame_len)


def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """
    Extract all valid frames from buffer; return (list of content byte strings, remaining buffer).
    Skips bytes until a valid header is found; invalid or truncated data leaves bytes in remainder.
    """
    out = []
    i = 0
    while i <= len(buffer) - MIN_FRAME_LEN:
        if buffer[i : i + HEADER_LEN] != HEADER:
            i += 1
            continue
        length = buffer[i + HEADER_LEN]
        if length > MAX_CONTENT_LEN:
            i += 1
            continue
        frame_len = HEADER_LEN + LENGTH_LEN + length + CHECKSUM_LEN
        if i + frame_len > len(buffer):
            break
        payload = buffer[i + HEADER_LEN + LENGTH_LEN : i + frame_len - 1]
        if checksum(payload) != buffer[i + frame_len - 1]:
            i += 1
            continue
        out.append(payload)
        i += frame_len
    return (out, buffer[i:])


# Message type constants (from doc examples)
MSG_HEARTBEAT = 0x01
MSG_TEMPERATURE = 0x10
