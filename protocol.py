#!/usr/bin/env python3
"""Binary framing protocol for sensor communication over TCP.

Message format (all values big-endian / network byte order):
    HEADER(2) | LENGTH(1) | CONTENT(n) | CHECKSUM(1)

    Header:   0xAA 0xDD  — fixed magic bytes marking the start of every message
    Length:   1 byte     — number of content bytes (0–255)
    Content:  n bytes    — payload; first byte is always the message type ID
    Checksum: 1 byte     — low byte of the sum of all content bytes

Message type IDs (first byte of content):
    0x01  Ping / heartbeat      content = [0x01]
    0x02  State transition       content = [0x02, reason, tag]
    0x10  Temperature           content = [0x10, sensor_id, value_hi, value_lo]
    0x11  RPM                   content = [0x11, sensor_id, value_hi, value_lo]
    0x12  Voltage               content = [0x12, sensor_id, value_hi, value_lo]
    0x13  Current               content = [0x13, sensor_id, value_hi, value_lo]
    0x14  Flow                  content = [0x14, sensor_id, value_hi, value_lo]

Sensor messages (0x1X) carry a 16-bit unsigned value in network byte order (big-endian).
Scale factors: temperature ×0.01 °C, voltage ×0.01 V, current ×0.01 A, flow ×0.01 L/min,
               RPM is an integer (scale ×1).

State transition messages (0x02):
    reason byte — why the system changed state:
        0x00  Bootup          → stop
        0x01  E-stop          → stop
        0x02  Software stop   → stop
        0x03  Critical sensor → stop
        0x04  Comm fail       → stop
        0x05  Ready           → ready
        0x06  Running         → running
    tag byte — 0x00 initial message, 0x01 acknowledgement
"""

HEADER = bytes([0xAA, 0xDD])

MSG_PING             = 0x01
MSG_STATE_TRANSITION = 0x02
MSG_TEMPERATURE      = 0x10
MSG_RPM              = 0x11
MSG_VOLTAGE          = 0x12
MSG_CURRENT          = 0x13
MSG_FLOW             = 0x14

# State transition reason codes
STATE_BOOTUP          = 0x00
STATE_ESTOP           = 0x01
STATE_SOFTWARE_STOP   = 0x02
STATE_CRITICAL_SENSOR = 0x03
STATE_COMM_FAIL       = 0x04
STATE_READY           = 0x05
STATE_RUNNING         = 0x06

_TYPE_NAMES: dict[int, str] = {
    MSG_PING:        "ping",
    MSG_TEMPERATURE: "temperature",
    MSG_RPM:         "rpm",
    MSG_VOLTAGE:     "voltage",
    MSG_CURRENT:     "current",
    MSG_FLOW:        "flow",
}

# Scale factor to convert raw uint16 → physical value
_SCALE: dict[int, float] = {
    MSG_TEMPERATURE: 0.01,
    MSG_RPM:         1.0,
    MSG_VOLTAGE:     0.01,
    MSG_CURRENT:     0.01,
    MSG_FLOW:        0.01,
}

_UNIT: dict[int, str] = {
    MSG_TEMPERATURE: "°C",
    MSG_RPM:         "rpm",
    MSG_VOLTAGE:     "V",
    MSG_CURRENT:     "A",
    MSG_FLOW:        "L/min",
}

# Reverse lookup: sensor type name → message type byte (excludes non-sensor types)
_NAME_TO_TYPE: dict[str, int] = {
    name: type_id
    for type_id, name in _TYPE_NAMES.items()
    if type_id not in (MSG_PING, MSG_STATE_TRANSITION)
}

# State transition reason code → human-readable name
_STATE_NAMES: dict[int, str] = {
    STATE_BOOTUP:          "bootup",
    STATE_ESTOP:           "e-stop",
    STATE_SOFTWARE_STOP:   "software stop",
    STATE_CRITICAL_SENSOR: "critical sensor",
    STATE_COMM_FAIL:       "comm fail",
    STATE_READY:           "ready",
    STATE_RUNNING:         "running",
}

# State transition reason code → resulting system state
_STATE_RESULT: dict[int, str] = {
    STATE_BOOTUP:          "stop",
    STATE_ESTOP:           "stop",
    STATE_SOFTWARE_STOP:   "stop",
    STATE_CRITICAL_SENSOR: "stop",
    STATE_COMM_FAIL:       "stop",
    STATE_READY:           "ready",
    STATE_RUNNING:         "running",
}


# ---------------------------------------------------------------------------
# Public error type
# ---------------------------------------------------------------------------

class ProtocolError(Exception):
    """Raised by encode() or decode() for any invalid argument or malformed message."""


# ---------------------------------------------------------------------------
# Public encode / decode
# ---------------------------------------------------------------------------

def encode(sensor_type: str, sensor_number: int, value: float) -> bytes:
    """Encode one sensor reading as a full protocol frame.

    Args:
        sensor_type:   one of "temperature", "rpm", "voltage", "current", "flow"
        sensor_number: 1-based sensor index (e.g. 1, 2, 3)
        value:         physical reading in natural units
                       (°C for temperature, rpm for RPM, V for voltage,
                        A for current, L/min for flow)

    Returns:
        Framed byte string ready to send over TCP.

    Raises:
        ProtocolError: if sensor_type is not recognised.

    Example:
        >>> encode("temperature", 1, 25.0).hex(" ").upper()
        'AA DD 04 10 01 09 C4 DE'
    """
    msg_type = _NAME_TO_TYPE.get(sensor_type.lower() if isinstance(sensor_type, str) else "")
    if msg_type is None:
        raise ProtocolError(
            f"Unknown sensor_type {sensor_type!r}. "
            f"Valid values: {sorted(_NAME_TO_TYPE)}"
        )
    scale = _SCALE[msg_type]
    raw = round(value / scale)
    return build_sensor(msg_type, sensor_number, raw)


def decode(hex_string: str) -> tuple[str, int, float]:
    """Decode a hex string of a complete framed sensor message.

    Args:
        hex_string: full protocol frame as hex characters, spaces optional.
                    E.g. "AA DD 04 10 01 09 C4 DE" or "AADD041001 09C4DE".

    Returns:
        (sensor_type, sensor_number, value) — same types accepted by encode().

    Raises:
        ProtocolError: for any structural problem, wrong message type, or bad checksum.
                       Use decode_state_transition() for state transition messages.
    """
    content = _parse_frame(hex_string)
    msg_type = content[0]
    if msg_type == MSG_PING:
        raise ProtocolError("Ping message does not carry sensor_type/sensor_number/value.")
    if msg_type == MSG_STATE_TRANSITION:
        raise ProtocolError("Use decode_state_transition() for state transition messages.")
    if msg_type not in _SCALE:
        raise ProtocolError(f"Unknown message type: 0x{msg_type:02X}.")
    if len(content) != 4:
        raise ProtocolError(f"Sensor payload must be 4 bytes, got {len(content)}.")
    sensor_number = content[1]
    raw_value = (content[2] << 8) | content[3]
    physical_value = raw_value * _SCALE[msg_type]
    return (_TYPE_NAMES[msg_type], sensor_number, physical_value)


# ---------------------------------------------------------------------------
# State transition encode  (one function per reason; is_ack selects tag byte)
# ---------------------------------------------------------------------------

def _encode_state(reason: int, is_ack: bool) -> bytes:
    """Internal helper: build a state transition frame."""
    tag = 0x01 if is_ack else 0x00
    content = bytes([MSG_STATE_TRANSITION, reason, tag])
    return build_message(content)


def encode_bootup(is_ack: bool = False) -> bytes:
    """State transition: system booted → stop state.

    Args:
        is_ack: False = initial message (tag 0x00), True = acknowledgement (tag 0x01).
    """
    return _encode_state(STATE_BOOTUP, is_ack)


def encode_estop(is_ack: bool = False) -> bytes:
    """State transition: e-stop triggered → stop state."""
    return _encode_state(STATE_ESTOP, is_ack)


def encode_software_stop(is_ack: bool = False) -> bytes:
    """State transition: software stop commanded → stop state."""
    return _encode_state(STATE_SOFTWARE_STOP, is_ack)


def encode_critical_sensor(is_ack: bool = False) -> bytes:
    """State transition: critical sensor out of range → stop state."""
    return _encode_state(STATE_CRITICAL_SENSOR, is_ack)


def encode_comm_fail(is_ack: bool = False) -> bytes:
    """State transition: communication failure detected → stop state."""
    return _encode_state(STATE_COMM_FAIL, is_ack)


def encode_ready(is_ack: bool = False) -> bytes:
    """State transition: system is ready → ready state."""
    return _encode_state(STATE_READY, is_ack)


def encode_running(is_ack: bool = False) -> bytes:
    """State transition: system started → running state."""
    return _encode_state(STATE_RUNNING, is_ack)


# ---------------------------------------------------------------------------
# State transition decode
# ---------------------------------------------------------------------------

def decode_state_transition(hex_string: str) -> tuple[str, str, bool]:
    """Decode a hex string of a complete framed state transition message.

    Args:
        hex_string: full protocol frame as hex characters, spaces optional.

    Returns:
        (reason, end_state, is_ack)
            reason:    human-readable reason string, e.g. "e-stop", "bootup"
            end_state: resulting system state — "stop", "ready", or "running"
            is_ack:    True if this is an acknowledgement, False if initial message

    Raises:
        ProtocolError: for any structural problem, wrong message type, or bad checksum.

    Examples:
        >>> decode_state_transition(encode_estop().hex())
        ('e-stop', 'stop', False)
        >>> decode_state_transition(encode_ready(is_ack=True).hex())
        ('ready', 'ready', True)
    """
    content = _parse_frame(hex_string)
    if content[0] != MSG_STATE_TRANSITION:
        raise ProtocolError(
            f"Expected state transition (0x02), got message type 0x{content[0]:02X}."
        )
    if len(content) != 3:
        raise ProtocolError(
            f"State transition payload must be 3 bytes, got {len(content)}."
        )
    reason = content[1]
    tag    = content[2]
    if reason not in _STATE_NAMES:
        raise ProtocolError(f"Unknown state transition reason: 0x{reason:02X}.")
    if tag not in (0x00, 0x01):
        raise ProtocolError(
            f"Invalid tag 0x{tag:02X}: expected 0x00 (initial) or 0x01 (ack)."
        )
    return (_STATE_NAMES[reason], _STATE_RESULT[reason], tag == 0x01)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _checksum(content: bytes) -> int:
    """Low byte of the sum of all content bytes."""
    return sum(content) & 0xFF


def _clamp_u16(value: int) -> int:
    return max(0, min(65535, value))


def _parse_frame(hex_string: str) -> bytes:
    """Parse and validate a complete protocol frame, returning its content bytes.

    Shared by decode() and decode_state_transition() to avoid duplicating the
    structural checks (hex parsing, header, length field, checksum).

    Raises:
        ProtocolError: for any structural problem before reaching the content.
    """
    # 1. Hex string → bytes
    clean = hex_string.strip().replace(" ", "").replace(",", "")
    if not clean:
        raise ProtocolError("Empty message.")
    try:
        data = bytes.fromhex(clean)
    except ValueError as exc:
        raise ProtocolError(f"Invalid hex string: {exc}") from exc

    # 2. Minimum size: header(2) + length(1) + 1 content byte + checksum(1) = 5
    if len(data) < 5:
        raise ProtocolError(
            f"Message too short: {len(data)} byte(s), minimum is 5."
        )

    # 3. Header
    if data[0] != 0xAA or data[1] != 0xDD:
        raise ProtocolError(
            f"Invalid header: expected AA DD, got {data[0]:02X} {data[1]:02X}."
        )

    # 4. Length field
    content_len = data[2]
    expected_total = 2 + 1 + content_len + 1
    if len(data) != expected_total:
        raise ProtocolError(
            f"Length mismatch: length field says {content_len} content byte(s) "
            f"({expected_total} bytes total), but received {len(data)} bytes."
        )

    # 5. Checksum
    content = data[3: 3 + content_len]
    received_cs = data[3 + content_len]
    computed_cs = _checksum(content)
    if computed_cs != received_cs:
        raise ProtocolError(
            f"Checksum mismatch: computed 0x{computed_cs:02X}, "
            f"received 0x{received_cs:02X}."
        )

    return content


def build_message(content: bytes) -> bytes:
    """Wrap *content* in a protocol frame.

    Returns: HEADER + LENGTH + CONTENT + CHECKSUM
    """
    cs = _checksum(content)
    return HEADER + bytes([len(content)]) + content + bytes([cs])


def build_ping() -> bytes:
    """Heartbeat / ping frame: AA DD 01 01 01."""
    return build_message(bytes([MSG_PING]))


def build_sensor(msg_type: int, sensor_id: int, raw_value: int) -> bytes:
    """Build a 4-byte-content sensor frame.

    raw_value is the already-scaled uint16 (e.g. 2500 for 25.00 °C).
    """
    raw_value = _clamp_u16(raw_value)
    content = bytes([msg_type, sensor_id, (raw_value >> 8) & 0xFF, raw_value & 0xFF])
    return build_message(content)


def encode_temperature(sensor_id: int, celsius: float) -> bytes:
    """Encode a temperature in °C as a protocol frame (resolution 0.01 °C)."""
    return build_sensor(MSG_TEMPERATURE, sensor_id, round(celsius * 100))


def encode_rpm(sensor_id: int, rpm: float) -> bytes:
    """Encode an RPM value as a protocol frame (integer RPM)."""
    return build_sensor(MSG_RPM, sensor_id, round(rpm))


def encode_voltage(sensor_id: int, volts: float) -> bytes:
    """Encode a voltage in V as a protocol frame (resolution 0.01 V)."""
    return build_sensor(MSG_VOLTAGE, sensor_id, round(volts * 100))


def encode_current(sensor_id: int, amps: float) -> bytes:
    """Encode a current in A as a protocol frame (resolution 0.01 A)."""
    return build_sensor(MSG_CURRENT, sensor_id, round(amps * 100))


def encode_flow(sensor_id: int, liters_per_min: float) -> bytes:
    """Encode a flow rate in L/min as a protocol frame (resolution 0.01 L/min)."""
    return build_sensor(MSG_FLOW, sensor_id, round(liters_per_min * 100))


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _decode_content(content: bytes) -> dict:
    """Parse validated content bytes into a message dict."""
    if not content:
        return {"type": "unknown", "raw": ""}
    msg_type = content[0]
    if msg_type == MSG_PING:
        return {"type": "ping"}
    if msg_type == MSG_STATE_TRANSITION and len(content) == 3:
        reason = content[1]
        tag    = content[2]
        return {
            "type":      "state_transition",
            "reason":    _STATE_NAMES.get(reason, f"0x{reason:02X}"),
            "end_state": _STATE_RESULT.get(reason, "unknown"),
            "is_ack":    tag == 0x01,
        }
    if msg_type in _SCALE and len(content) == 4:
        sensor_id = content[1]
        raw_value = (content[2] << 8) | content[3]
        value = raw_value * _SCALE[msg_type]
        return {
            "type":      _TYPE_NAMES[msg_type],
            "sensor_id": sensor_id,
            "value":     value,
            "unit":      _UNIT[msg_type],
        }
    return {"type": "unknown", "raw": content.hex(" ")}


def describe(msg: dict) -> str:
    """Return a human-readable one-line description of a decoded message dict."""
    t = msg.get("type", "unknown")
    if t == "ping":
        return "Ping"
    if t == "state_transition":
        reason    = msg.get("reason", "unknown")
        end_state = msg.get("end_state", "unknown")
        tag       = "ack" if msg.get("is_ack") else "initial"
        return f"State → {end_state}  reason: {reason}  [{tag}]"
    if "sensor_id" in msg:
        unit = msg.get("unit", "")
        val  = msg.get("value", 0.0)
        sid  = msg.get("sensor_id", 0)
        fmt  = ".0f" if unit == "rpm" else ".2f"
        return f"{t.capitalize()} sensor {sid}: {val:{fmt}} {unit}"
    return f"Unknown: {msg.get('raw', '')}"


class StreamParser:
    """Stateful parser for a TCP byte stream.

    TCP can deliver data in arbitrary chunks; this class reassembles complete
    protocol messages across recv() calls.  It:

    - scans for the 0xAA 0xDD header
    - discards any bytes before the header (framing sync)
    - waits until a full message (header + length + content + checksum) is buffered
    - validates the checksum; drops the message silently if it fails
    - returns all complete, valid messages from a single feed() call

    Usage:
        parser = StreamParser()
        msgs = parser.feed(received_bytes)  # list of decoded dicts
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        """Discard buffered bytes (call when a new connection is established)."""
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        """Consume *data* and return a (possibly empty) list of decoded messages."""
        self._buf.extend(data)
        messages: list[dict] = []

        while True:
            pos = self._find_header()

            if pos == -1:
                # No header pattern in buffer; keep last byte only if it could
                # be the first byte of a header arriving in the next chunk.
                if self._buf and self._buf[-1] == 0xAA:
                    self._buf = bytearray([0xAA])
                else:
                    self._buf = bytearray()
                break

            if pos > 0:
                # Discard garbage before the header.
                self._buf = self._buf[pos:]

            # Buffer starts with 0xAA 0xDD.
            # Need at least: header(2) + length(1) = 3 bytes to read the length field.
            if len(self._buf) < 3:
                break  # Wait for more data.

            content_len = self._buf[2]
            total = 2 + 1 + content_len + 1  # header + length_byte + content + checksum

            if len(self._buf) < total:
                break  # Incomplete message; wait for more data.

            content = bytes(self._buf[3: 3 + content_len])
            received_cs = self._buf[3 + content_len]

            if _checksum(content) != received_cs:
                # Checksum mismatch — message is corrupted.  Discard just the
                # first header byte and re-scan so we don't skip a real header
                # that might start one byte later.
                self._buf = self._buf[2:]
                continue

            messages.append(_decode_content(content))
            self._buf = self._buf[total:]

        return messages

    def _find_header(self) -> int:
        """Return index of the first 0xAA 0xDD pair, or -1."""
        for i in range(len(self._buf) - 1):
            if self._buf[i] == 0xAA and self._buf[i + 1] == 0xDD:
                return i
        return -1
