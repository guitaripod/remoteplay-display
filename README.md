# remoteplay-display

Routes Steam Remote Play sessions to a dummy HDMI plug on KDE Plasma Wayland, then puts your desktop back exactly as it was.

When a client connects, the host swaps to the dummy plug as its **only** output, at the resolution and refresh rate that best feeds that client. Your real monitors go dark and stay dark. When the session ends, the previous layout is restored — same modes, positions, scales and priorities.

## Why

A dummy plug does not stop your monitors from lighting up — Linux happily scans out to a powered-off display, so nothing was waking them in the first place. The real problems it solves are:

- **The game renders at your desktop's resolution.** Without this, a game streamed to a Steam Deck renders at your monitor's native 4K165 and gets scaled down to 1280x800. That is a large amount of GPU and encoder work thrown away.
- **Powering monitors off mid-session reshuffles the desktop.** DisplayPort and HDMI both drop the link when a display loses power, which makes KWin re-lay-out your outputs underneath a running game.

Making the dummy the sole output during a session fixes both, and makes the physical power state of your monitors irrelevant.

## How it detects a session

Steam's streaming host loads a PipeWire null sink named `steam-streaming-playback` the moment a Remote Play session starts, repoints the default audio sink at it, and records from its monitor. It unloads the sink when the session ends.

That sink is the trigger. It is live system state rather than a log line, so it can be watched as an event (`pactl subscribe`) *and* re-queried at any time to recover from a missed event or a crash. Tailing Steam's `streaming_log.txt` was the obvious alternative and is strictly worse — the format is undocumented and can't be re-read to answer "what is true right now".

Steam writes `Maximum capture: WxH F FPS` a few milliseconds *before* loading the sink, so the connected client's ceiling is already readable when the trigger fires. A Steam Deck OLED reports `1280x800 89.00 FPS`.

## Mode selection

With `mode = auto`, the dummy's mode is scored per client and picked at session start. The key, maximized lexicographically:

1. Can the mode feed the client's frame rate (`refresh >= client_fps - 1`)
2. Aspect ratio proximity to the client
3. Does the mode cover the client's resolution
4. Pixel count proximity to the client
5. Highest refresh rate

Refresh rate outranks resolution deliberately. Downscaling 1080p to a Deck's 800p costs very little perceptually; 60 fps versus 90 fps is felt immediately. Aspect ratio outranks pixel count because otherwise a 16:9 client can be handed a 21:9 host mode that merely happens to have a closer pixel count.

Set `mode` to something like `2560x1440@60` to pin it instead.

## Install

Requires KDE Plasma 6 (`kscreen-doctor`), PipeWire (`pactl`), Python 3.11+, and a dummy plug.

```sh
git clone https://github.com/guitaripod/remoteplay-display
cd remoteplay-display
./install.sh
```

`install.sh` symlinks the script into `~/.local/bin`, installs the user service, seeds `~/.config/remoteplay-display/config.ini`, enables the service and runs `doctor`.

Then set `dummy` in the config to your dummy plug's connector. `remoteplay-display doctor` lists every connector with its EDID product name, which usually makes the dummy obvious:

```
connectors:
  HDMI-A-1   connected     edid='Odyssey G5'
  HDMI-A-2   connected     edid='Mi TV'  <- configured dummy
  DP-3       connected     edid='Odyssey Ark'
```

## Usage

```sh
remoteplay-display status    # current mode, session state, output layout
remoteplay-display on        # switch to dummy-only now
remoteplay-display off       # restore the saved layout
remoteplay-display toggle
remoteplay-display doctor    # self-check
```

The service does this automatically; the manual commands are for testing, or for pinning the host into remote mode ahead of time.

A layout applied by hand is tagged `manual` and a layout applied by the daemon is tagged `auto`. The daemon only ever restores its own — so a manual `on` survives a daemon restart, and stopping the service cleanly returns anything it switched.

## Configuration

`~/.config/remoteplay-display/config.ini`

| key | default | meaning |
| --- | --- | --- |
| `dummy` | `HDMI-A-2` | connector name of the dummy plug |
| `mode` | `auto` | `auto`, or a pinned mode like `1920x1080@120` |
| `fallback_mode` | `1920x1080@120` | used when no recent client hint is available |
| `auto` | `true` | switch automatically on session start/end |
| `inhibit_idle` | `true` | hold an idle/sleep inhibitor during a session |
| `steam_log` | `~/.steam/steam/logs/streaming_log.txt` | where to read the client capture hint |
| `capture_hint_max_age` | `120` | seconds before a capture hint is considered stale |
| `settle_timeout` | `10.0` | seconds to wait for KWin to apply a layout |

## Design notes

**Atomic layout changes.** Every switch is a single `kscreen-doctor` invocation. Mid-transition KWin logs `There are no outputs - creating placeholder screen`; atomicity is the only reason that isn't a visible failure.

**Mode ids, not mode names.** A dummy plug commonly exposes several distinct modes sharing one name — one tested plug has two different modes both called `1920x1080@60`. Layouts are therefore snapshotted as `(width, height, refresh)` and re-resolved against the live mode list at apply time, which also survives connector renumbering across a replug.

**Verification, not fire-and-forget.** After applying, the state is polled every 250ms until the expected outputs are enabled and the dummy's mode size matches, with one retry before failing loudly. Position and priority are deliberately not asserted, because KWin normalizes output geometry and asserting it produces false failures.

**Idle inhibition.** A detached child holds `org.freedesktop.ScreenSaver.Inhibit` plus a logind `idle:sleep` block for the duration of a session. Gamepad-only input may not reset KWin's idle timer, and a blanked dummy means a black stream with no local display to notice it on.

**Crash recovery.** State is flock'd JSON. A `SIGKILL` leaves it stale; the next start reconciles "marked active, owned by the daemon, but no session sink" and restores. Restore skips outputs that are no longer connected, and if nothing in the snapshot is connected it keeps the dummy enabled rather than leaving KWin with zero outputs.

## Known limits

**Do not add `-pipewire` to Steam's launch options on KDE Wayland**, even though Steam's own log suggests it. [steam-for-linux#13348](https://github.com/ValveSoftware/steam-for-linux/issues/13348) reports stale frames being injected into the stream via the PipeWire capture path on Plasma 6.7. Without it Steam captures games through its Vulkan layer, which is unaffected by any of this.

**Games Steam's Vulkan capture layer doesn't attach to** — typically emulators added as non-Steam shortcuts — fail with `k_ECaptureFailedReasonPipewireRequired` and stream nothing. That is a capture problem, not a display problem, and this tool does not address it.

**Dummy plugs lie about 4K.** Many advertise 4K60 but only offer it as YCbCr 4:2:0, with RGB capped at 4K30 by a 297 MHz TMDS limit. Linux does not expose 4:2:0-only modes, so the driver never offers 4K60. Check `remoteplay-display doctor` for what your plug actually exposes before assuming.

**Sunshine/Moonlight sessions are not detected**, since they don't create the Steam null sink. Sunshine's `global_prep_cmd` can call `remoteplay-display on` and `off` instead.

## License

GPL-3.0
