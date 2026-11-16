Communication framework: TCP
TCP (Transmission Control Protocol) is a core internet protocol that enables reliable, ordered, and error-checked delivery of data between applications over an IP network. It establishes a connection using a handshake process, manages data flow with acknowledgments and retransmissions, and adjusts sending rates to prevent congestion. By ensuring that data arrives intact and in the correct order, TCP provides a dependable communication layer for applications like web browsing, email, and file transfer.

We will be implementing a rigid protocol than we technically need but it is good practice to understand communication between computers. 

Challenges!
Our communication protocol needs to be resilient to delayed messages, dropped messages, corrupted messages. 
It needs to be able to identify and react to lost connection, it needs to be able to monitor the health of the connection. 

We need to be able to identify the start of the message, encode and decode the contents of messages, and be able to verify the integrity of the message (if there was corruption)

Priorities
We do not need to guarantee that every message goes through. 
We CANNOT read corrupted messages. 
We need to be able to determine quickly if communications have broken and react appropriately on both sides. 

General Notes
In this document we will be using Hexadecimal to represent the bytes we are sending. 

A bit is the smallest unit of data and can be either 0 or 1, while a byte is a group of 8 bits and is the basic unit used to represent values like characters, numbers, and sensor readings. Because binary numbers are hard to read, hexadecimal (base-16) is commonly used as a human-friendly shorthand for binary, where one hex digit represents exactly 4 bits (for example, 0xAF = 10101111). This makes hex especially useful for viewing raw data, memory, and network or hardware-level values.

Endianness describes the order in which multi-byte values are stored or transmitted: big-endian stores the most significant byte first, while little-endian stores the least significant byte first. Most modern CPUs (including x86 and ESP32) are little-endian internally, but many network protocols—including TCP/IP—use big-endian, often called network byte order. Because of this, software commonly converts between host byte order and network byte order when sending or receiving multi-byte values. 

A single byte always has the most significant bit on the left and least significant bit on the right (like a normal number). So the byte 0100 1011 represents the integer value of 2^6+2^3+2^2+2^0=77

Consider a 16-bit integer with the bit pattern
 00010010 00110100.
 Each group of 8 bits is one byte, and each bit position represents a power of two.
In big-endian, the first byte (00010010) is the most significant byte, so it represents the higher-value bits. This byte equals 16+2=18, and the second byte (00110100) equals 32+16+4=52 The integer value is computed as 18×256+52=4660
In little-endian, the bytes are stored in reverse order as
 00110100 00010010. Now the first byte is treated as the least significant byte (value 52), and the second byte as the most significant byte (value 18). The calculation is 18×256+52=4660.
 The bits in memory are arranged differently, but the integer value is reconstructed correctly based on the agreed byte order.
Two hex characters represents a single byte. Each of them represents one “half” of the byte. 

Given this conversion table the byte 1D is 0001 1101 in binary. A4 would be 1010 0100. And so on. 

When working with bytes you will often see a 0x notation or 0b notation. 
The 0x prefix is used to indicate that a number is written in hexadecimal (base 16), while the 0b prefix indicates a binary (base 2) number. These prefixes tell the reader and the programming language how to interpret the digits that follow and prevent confusion with decimal values. For example, 0x10 represents sixteen in decimal, while 0b10 represents two, even though both look similar without their prefixes

Protocol
Overall structure
This will define the overall message structure

Message header | message length | contents | check sum

Message header
We need to be able to identify the header of messages so we don’t confuse the start of one with the end of others. 
The message header can be any arbitrary bytes that everyone agrees on. For now we will go ahead and use 0xAADD

Message length
To correctly identify the ending of a message we need to know how long it is. This part of the message will only be a single byte meaning we are limiting the “contents” of our message to 255 bytes long. 

Contents
This section is the actual payload of the message. The first byte of the contents is always the message type ID. Remaining bytes depend on the type. Multi-byte numeric values are sent big-endian (most significant byte first, network byte order).

Message type IDs:
  0x01  Ping / heartbeat
  0x10  Temperature
  0x11  RPM
  0x12  Voltage
  0x13  Current
  0x14  Flow

Sensor message format (temperature, RPM, voltage, current, flow):
Each sensor message has: byte 1 = message type (0x10 to 0x14); byte 2 = sensor number (e.g. 0x01, 0x02, 0x03); bytes 3-4 = value as 16-bit unsigned integer, big-endian. Scale and min/max per type:

  Temperature (0x10): 4 bytes contents: type 0x10, sensor (0x01/0x02/0x03), then value in 0.01 C (2 bytes big-endian). Example: 1 C = 100 = 0x00 0x64; 25 C = 2500 = 0x09 0xC4. Value range 0x0000 to 0xFFFF (65535), i.e. 0 to 655.35 C.
  RPM (0x11): 4 bytes contents: type 0x11, sensor (0x01), then RPM as integer (2 bytes big-endian). Value range 0x0000 to 0xFFFF (65535).
  Voltage (0x12): 4 bytes contents: type 0x12, sensor (0x01/0x02), then value in 0.01 V (2 bytes big-endian). Example: 12.50 V = 1250 = 0x04 0xE2. Value range 0x0000 to 0xFFFF (65535), i.e. 0 to 655.35 V.
  Current (0x13): 4 bytes contents: type 0x13, sensor (0x01/0x02), then value in 0.01 A (2 bytes big-endian). Value range 0x0000 to 0xFFFF (65535), i.e. 0 to 655.35 A.
  Flow (0x14): 4 bytes contents: type 0x14, sensor (0x01), then value in 0.01 L/min (2 bytes big-endian). Value range 0x0000 to 0xFFFF (65535), i.e. 0 to 655.35 L/min.

Checksum is the low byte of the sum of all content bytes.

Check sum
A checksum is a simple error-detection method that helps verify whether data was corrupted during transmission. The sender adds together the hexadecimal values of all bytes in the message and keeps only the last byte (the lowest 8 bits) of the sum; this byte is sent as the checksum. The receiver repeats the same calculation and compares the result to the received checksum.

For example, suppose a message contains three bytes: 0x12, 0x34, and 0x56. Adding them gives
 0x12 + 0x34 + 0x56 = 0x9C.
 Since this already fits in one byte, the checksum is 0x9C. If the sum had been larger than one byte (for example 0x1A3), only the last byte (0xA3) would be kept. If the receiver’s computed checksum does not match, it indicates that the data was corrupted or altered in transit.
	
Example messages 
For a heartbeat or ping message, the contents consist of a single byte with the value 0x01, which represents the heartbeat message type. Because the checksum is calculated using only the contents, the checksum is simply the sum of that one byte, which is also 0x01. The complete heartbeat message in hexadecimal is therefore 0xAA DD 01 01 01, where the final 0x01 is the checksum.
For a temperature message, let’s contents are four bytes: 0x10 (type), 0x01 (sensor 1), and value 2500 (25.00 °C in 0.01 °C) as two bytes 0x09 0xC4. The length byte is 0x04. Sum of contents: 0x10 + 0x01 + 0x09 + 0xC4 = 0xDE → checksum 0xDE. The full temperature message is 0xAA DD 04 10 01 09 C4 DE, with each field defining how the message should be interpreted.


