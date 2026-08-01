#!/usr/bin/env python3
"""Rebuild the dummy EDID with the fastest 16:10 modes its TMDS ceiling allows.

The stock synthetic EDID tops out at 2560x1600@90, which wastes a 144 Hz client.
HDMI TMDS caps at 600 MHz without FRL, and a passive dummy plug cannot negotiate
FRL, so every mode here is CVT reduced-blanking sized to stay under that wall.
"""
import struct
from pathlib import Path

SRC = Path("/lib/firmware/edid/remoteplay-dummy.edid")
DST = Path.home() / ".local/share/remoteplay-display/dummy-fast.edid"

TMDS_MHZ = 600
PANEL_MAX_NITS = 800
PANEL_MIN_NITS = 0.6154
RANGE_DESC = 90
RANGE_MAX_V = RANGE_DESC + 6
RANGE_MAX_H = RANGE_DESC + 8
RANGE_MAX_CLOCK = RANGE_DESC + 9
HF_VSDB_MAX_TMDS = 0x91 - 128
MIN_VBLANK_SECONDS = 460e-6


def cvt_rb(hact, vact, refresh, hblank=160, hfront=48, hsync=32, vfront=3, vsync=5):
    """CVT reduced-blanking v1 timings honouring the 460 us minimum vertical blanking."""
    htotal = hact + hblank
    vb = 1
    while True:
        pclk = htotal * (vact + vb) * refresh
        if vb * htotal / pclk >= MIN_VBLANK_SECONDS:
            break
        vb += 1
    return dict(hact=hact, vact=vact, htotal=htotal, vtotal=vact + vb, hblank=hblank,
                vblank=vb, hfront=hfront, hsync=hsync, vfront=vfront, vsync=vsync,
                pclk=htotal * (vact + vb) * refresh, refresh=refresh)


def dtd(t, hmm, vmm):
    pc = round(t["pclk"] / 10_000)
    assert 0 < pc <= 0xFFFF, f"{t['hact']}x{t['vact']}@{t['refresh']} overflows the DTD clock field"
    b = bytearray(18)
    b[0:2] = struct.pack("<H", pc)
    b[2] = t["hact"] & 0xFF
    b[3] = t["hblank"] & 0xFF
    b[4] = ((t["hact"] >> 8) << 4) | (t["hblank"] >> 8)
    b[5] = t["vact"] & 0xFF
    b[6] = t["vblank"] & 0xFF
    b[7] = ((t["vact"] >> 8) << 4) | (t["vblank"] >> 8)
    b[8] = t["hfront"] & 0xFF
    b[9] = t["hsync"] & 0xFF
    b[10] = ((t["vfront"] & 0xF) << 4) | (t["vsync"] & 0xF)
    b[11] = ((t["hfront"] >> 8) << 6) | ((t["hsync"] >> 8) << 4) | \
            ((t["vfront"] >> 4) << 2) | (t["vsync"] >> 4)
    b[12] = hmm & 0xFF
    b[13] = vmm & 0xFF
    b[14] = ((hmm >> 8) << 4) | (vmm >> 8)
    b[17] = 0x1A
    return bytes(b)


def checksum(block):
    b = bytearray(block)
    b[127] = (-sum(b[:127])) & 0xFF
    return bytes(b)


def _luminance_code(nits):
    """CTA-861 luminance encoding: nits = 50 * 2**(code/32)."""
    from math import log2
    return max(0, min(255, round(32 * log2(nits / 50))))


def _min_luminance_code(nits, max_nits):
    """CTA-861 min-luminance encoding: nits = max_nits * (code/255)**2 / 100."""
    from math import sqrt
    return max(0, min(255, round(255 * sqrt(nits * 100 / max_nits))))


def hdr_blocks(max_nits, min_nits):
    """Colorimetry + HDR Static Metadata CTA blocks, sized to the client panel.

    Without these KDE cannot put the dummy into HDR, Sunshine reports 'Sent HDR
    mode: false', and the client never negotiates HEVC Main10.
    """
    colorimetry = bytes([0xE3, 0x05, 0xC0, 0x00])
    hdr = bytes([
        0xE6, 0x06,
        0x0F,  # SDR | traditional HDR | SMPTE ST2084 | HLG
        0x01,  # static metadata type 1
        _luminance_code(max_nits),
        _luminance_code(max_nits),
        _min_luminance_code(min_nits, max_nits),
    ])
    return colorimetry + hdr


W10, H10 = 520, 325
W9, H9 = 520, 292

MODES = [
    (cvt_rb(2400, 1500, 144), W10, H10),
    (cvt_rb(2560, 1600, 120), W10, H10),
    (cvt_rb(3840, 2160, 60), W9, H9),
    (cvt_rb(2560, 1440, 120), W9, H9),
    (cvt_rb(1920, 1200, 144), W10, H10),
    (cvt_rb(1920, 1200, 120), W10, H10),
    (cvt_rb(1920, 1080, 120), W9, H9),
]


def main():
    src = bytearray(SRC.read_bytes())
    assert len(src) == 256, "expected a 128-byte base block plus one CTA extension"
    base, ext = bytearray(src[:128]), bytearray(src[128:])

    for t, _, _ in MODES:
        print(f"{t['hact']}x{t['vact']}@{t['refresh']:<4} {t['pclk']/1e6:7.2f} MHz "
              f"{t['pclk']/t['htotal']/1e3:6.1f} kHz")
        assert t["pclk"] / 1e6 <= TMDS_MHZ, "exceeds the 600 MHz TMDS ceiling"

    base[54:72] = dtd(*MODES[0])
    base[72:90] = dtd(*MODES[1])

    assert base[RANGE_DESC:RANGE_DESC + 4] == b"\x00\x00\x00\xfd", "range-limit descriptor moved"
    base[RANGE_MAX_V] = 150
    base[RANGE_MAX_H] = 250
    base[RANGE_MAX_CLOCK] = TMDS_MHZ // 10

    assert ext[1] == 0x03 and ext[2] == 0x17, "unexpected CTA extension layout"
    ext[HF_VSDB_MAX_TMDS] = TMDS_MHZ // 5

    extra = hdr_blocks(PANEL_MAX_NITS, PANEL_MIN_NITS)
    dtd_start = ext[2]
    ext[dtd_start:dtd_start] = extra
    del ext[128:]
    ext[2] = dtd_start + len(extra)

    p = ext[2]
    for t, hmm, vmm in MODES[2:]:
        ext[p:p + 18] = dtd(t, hmm, vmm)
        p += 18
    assert p <= 127, "too many detailed timings for one extension block"
    ext[p:127] = bytes(127 - p)

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_bytes(checksum(base) + checksum(ext))
    print(f"\nwrote {DST}")


if __name__ == "__main__":
    main()
