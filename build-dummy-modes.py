#!/usr/bin/env python3
"""Rebuild the dummy EDID with one exact-cadence mode per client this host streams to.

The stock synthetic EDID tops out at 2560x1600@90, which wastes a 144 Hz client.
HDMI TMDS caps at 600 MHz without FRL, and a passive dummy plug cannot negotiate
FRL, so every mode here is reduced-blanking sized to stay under that wall.
"""
import argparse
import struct
import subprocess
import tempfile
from pathlib import Path

GENERATOR = Path(__file__).resolve().parent / "dummy-edid"
DST = Path("/lib/firmware/edid/remoteplay-dummy.edid")

TMDS_MHZ = 600
MIDBW_MHZ = 340
LOWBW_MHZ = 160
PANEL_MAX_NITS = 800
PANEL_MIN_NITS = 0.6154
RANGE_DESC = 90
RANGE_MAX_V = RANGE_DESC + 6
RANGE_MAX_H = RANGE_DESC + 8
RANGE_MAX_CLOCK = RANGE_DESC + 9
HF_VSDB_MAX_TMDS = 0x91 - 128
MIN_VBLANK_SECONDS = 460e-6
HBLANK_MIN = 160
HBLANK_MAX = 560
VBLANK_MAX = 400
HBACK = 80


def cvt_rb(hact, vact, refresh, hsync=32, vfront=3, vsync=5):
    """Reduced-blanking timings whose refresh rate is exactly integral.

    EDID stores the pixel clock in 10 kHz units, so a blanking geometry whose
    htotal*vtotal*refresh is not a multiple of 10000 gets rounded at encode time and
    the output scans at a slightly wrong rate. Sunshine samples the scanout on its own
    timer, so that residue shows up as a skipped frame every few seconds no matter the
    bitrate. Search the blanking space for the cheapest geometry that divides cleanly,
    staying at or above the CVT-RB 160-pixel horizontal blank and the 460 us minimum
    vertical blank.
    """
    best = None
    for htotal in range(hact + HBLANK_MIN, hact + HBLANK_MAX + 1):
        for vtotal in range(vact + 8, vact + VBLANK_MAX + 1):
            if (vtotal - vact) / (vtotal * refresh) < MIN_VBLANK_SECONDS:
                continue
            pclk = htotal * vtotal * refresh
            if pclk != int(pclk) or int(pclk) % 10_000 or pclk > TMDS_MHZ * 1e6:
                continue
            key = (int(pclk), abs(htotal - hact - HBLANK_MIN))
            if best is None or key < best[0]:
                best = (key, htotal, vtotal)
    if best is None:
        raise SystemExit(f"no exact-cadence timing fits {hact}x{vact}@{refresh} under "
                         f"{TMDS_MHZ} MHz")
    _, htotal, vtotal = best
    hblank, vblank = htotal - hact, vtotal - vact
    return dict(hact=hact, vact=vact, htotal=htotal, vtotal=vtotal, hblank=hblank,
                vblank=vblank, hfront=hblank - hsync - HBACK, hsync=hsync,
                vfront=vfront, vsync=vsync, pclk=htotal * vtotal * refresh,
                refresh=refresh)


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

def full_modes():
    """One exact-cadence mode per client, sized for a link that can carry 600 MHz."""
    return [
        (cvt_rb(2400, 1500, 144), W10, H10),
        (cvt_rb(2560, 1600, 120), W10, H10),
        (cvt_rb(3840, 2160, 60), W9, H9),
        (cvt_rb(2560, 1440, 120), W9, H9),
        (cvt_rb(1920, 1200, 144), W10, H10),
        (cvt_rb(1920, 1200, 89), W10, H10),
        (cvt_rb(1920, 1080, 120), W9, H9),
    ]


def midbw_modes():
    """Targets for a plug measured at roughly 340 MHz of pixel clock.

    Tuned for a Steam Deck OLED, the only client this host streams to: 90 Hz panel, so the
    1920x1200 modes are exact cadence for a 90 fps request and the 2:1 ratio a 60 fps request
    needs, while the 1280x800 pair gives the scorer a panel-sized option. 1920x1200@89 is gone
    on purpose: it approximated 90 Hz closely enough to win the cadence tie against 120 Hz, and
    an exact 90 now beats it outright.
    """
    return [
        (cvt_rb(1920, 1200, 90), W10, H10),
        (cvt_rb(1920, 1200, 120), W10, H10),
        (cvt_rb(1280, 800, 90), W10, H10),
        (cvt_rb(1280, 800, 120), W10, H10),
        (cvt_rb(2560, 1600, 75), W10, H10),
    ]


def lowbw_modes():
    """The same client targets derated for a dummy plug whose DP link tops out near 160 MHz.

    A passive DisplayPort dummy is not the bandwidth-transparent EEPROM an HDMI one is: the
    driver still link-trains against the plug's DPCD and rejects every mode the trained link
    cannot carry, no matter what the overridden EDID advertises. A plug measured at roughly
    4 Gbit/s leaves ~160 MHz of pixel clock, so the full table collapses to the 640x480
    fallback. These timings keep the exact-cadence property Sunshine needs while fitting under
    that wall; swap back to the full table once the plug can carry it.
    """
    return [
        (cvt_rb(1920, 1200, 60), W10, H10),
        (cvt_rb(1920, 1080, 60), W9, H9),
        (cvt_rb(2560, 1600, 30), W10, H10),
        (cvt_rb(2560, 1440, 30), W9, H9),
        (cvt_rb(1680, 1050, 75), W10, H10),
        (cvt_rb(1280, 800, 120), W10, H10),
        (cvt_rb(1280, 720, 120), W9, H9),
    ]


def pristine_base() -> bytes:
    """Generate the stock synthetic EDID rather than reading the installed one.

    The installed file is this script's own output; reading it back would insert the
    HDR blocks a second time and shift every detailed timing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "base.edid"
        subprocess.run([str(GENERATOR), "write", "--out", str(out)], check=True,
                       capture_output=True)
        return out.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DST)
    parser.add_argument("--max-clock", type=int, default=TMDS_MHZ,
                        help="pixel-clock ceiling in MHz the link can actually carry")
    args = parser.parse_args()

    ceiling = args.max_clock
    if ceiling > MIDBW_MHZ:
        modes = full_modes()
    elif ceiling > LOWBW_MHZ:
        modes = midbw_modes()
    else:
        modes = lowbw_modes()

    src = bytearray(pristine_base())
    assert len(src) == 256, "expected a 128-byte base block plus one CTA extension"
    base, ext = bytearray(src[:128]), bytearray(src[128:])

    for t, _, _ in modes:
        print(f"{t['hact']}x{t['vact']}@{t['refresh']:<4} {t['pclk']/1e6:7.2f} MHz "
              f"{t['pclk']/t['htotal']/1e3:6.1f} kHz  "
              f"{t['htotal']}x{t['vtotal']} blank {t['hblank']}/{t['vblank']}")
        assert t["pclk"] / 1e6 <= ceiling, f"exceeds the {ceiling} MHz ceiling"
        assert int(t["pclk"]) % 10_000 == 0, "pixel clock is not exactly encodable"

    base[54:72] = dtd(*modes[0])
    base[72:90] = dtd(*modes[1])

    assert base[RANGE_DESC:RANGE_DESC + 4] == b"\x00\x00\x00\xfd", "range-limit descriptor moved"
    base[RANGE_MAX_V] = 150
    base[RANGE_MAX_H] = 250
    base[RANGE_MAX_CLOCK] = ceiling // 10

    assert ext[1] == 0x03 and ext[2] == 0x17, "unexpected CTA extension layout"
    ext[HF_VSDB_MAX_TMDS] = ceiling // 5

    extra = hdr_blocks(PANEL_MAX_NITS, PANEL_MIN_NITS)
    dtd_start = ext[2]
    ext[dtd_start:dtd_start] = extra
    del ext[128:]
    ext[2] = dtd_start + len(extra)

    p = ext[2]
    for t, hmm, vmm in modes[2:]:
        ext[p:p + 18] = dtd(t, hmm, vmm)
        p += 18
    assert p <= 127, "too many detailed timings for one extension block"
    ext[p:127] = bytes(127 - p)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(checksum(base) + checksum(ext))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
