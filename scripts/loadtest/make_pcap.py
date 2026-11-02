#!/usr/bin/env python3
"""CH-465 load test: the caller's audio, as SIPp plays it (play_pcap_audio).

    python3 make_pcap.py /root/loadtest/tone-ulaw.pcap 90

A pcap of RTP packets, G.711 u-law (payload 0), 20 ms each, a soft tone for
the given number of seconds - so every test call carries real audio through
Asterisk (relayed, recorded) like a live call does.
"""
import math
import struct
import sys


def ulaw(sample):
    """16-bit linear to G.711 u-law."""
    bias, clip = 0x84, 32635
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), clip) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not sample & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    s = sum(struct.unpack(f'!{len(data) // 2}H', data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def main(path, seconds):
    packets = int(seconds * 50)
    with open(path, 'wb') as f:
        f.write(struct.pack('<IHHiIII', 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))   # Ethernet
        t = 0
        for n in range(packets):
            audio = bytes(ulaw(int(6000 * math.sin(2 * math.pi * 440 * (t + i) / 8000))) for i in range(160))
            rtp = struct.pack('!BBHII', 0x80, 0, n & 0xFFFF, t & 0xFFFFFFFF, 0x41565831) + audio
            udp = struct.pack('!HHHH', 6000, 6000, 8 + len(rtp), 0) + rtp
            ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(udp), n & 0xFFFF, 0, 64, 17, 0,
                             bytes([127, 0, 0, 2]), bytes([127, 0, 0, 1]))
            ip = ip[:10] + struct.pack('!H', checksum(ip)) + ip[12:]
            frame = b'\0' * 6 + b'\0' * 6 + b'\x08\x00' + ip + udp
            usec = n * 20000
            f.write(struct.pack('<IIII', usec // 1000000, usec % 1000000, len(frame), len(frame)) + frame)
            t += 160
    print(f'{path}: {packets} packets, {seconds} s of audio')


if __name__ == '__main__':
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 90)
