# remoteplay-display

Routes Steam Remote Play and Sunshine sessions to a dummy HDMI plug on KDE Plasma Wayland. While a client is connected the dummy becomes the host's **only** output, at whatever resolution and refresh rate best feeds that client; when the session ends the previous layout comes back exactly — same modes, positions, scales, priorities.

## Why

A dummy plug does not stop your monitors from lighting up — Linux happily scans out to a powered-off display, so nothing was waking them in the first place. What it actually fixes:

- **The game renders at your desktop's resolution.** A game streamed to a Steam Deck otherwise renders at your monitor's native 4K165 and gets scaled down to 1280x800 — a lot of GPU and encoder work thrown away.
- **Powering monitors off mid-session reshuffles the desktop.** DisplayPort and HDMI both drop the link when a display loses power, and KWin re-lays-out your outputs underneath the running game.

Making the dummy the sole output solves both, and makes the monitors' power state irrelevant.

## How it detects a session

Steam's streaming host loads a PipeWire null sink named `steam-streaming-playback` when a session starts and unloads it when the session ends. That sink is the trigger — being live system state rather than a log line, it can be watched as an event (`pactl subscribe`) *and* re-queried at any time to recover from a missed event or a crash, which tailing `streaming_log.txt` cannot.

Steam does write `Maximum capture: WxH F FPS` a few milliseconds before loading the sink, so the client's ceiling is readable the instant the trigger fires. A Steam Deck OLED reports `1280x800 89.00 FPS`.

Sunshine has no equivalent marker and needs none: it runs `global_prep_cmd` around every session and hands the stream geometry to it in the environment, so it calls this tool directly. See [Sunshine / Moonlight](#sunshine--moonlight).

## Mode selection

The client's geometry comes from the first source that answers: an explicit `--client WxH@FPS`, Sunshine's `SUNSHINE_CLIENT_*` environment, then Steam's capture line.

With `mode = auto` the dummy's mode is scored per client at session start, maximizing lexicographically:

1. Can the mode feed the client's frame rate (`refresh >= client_fps - 1`)
2. Aspect ratio proximity to the client
3. Cadence: how close `refresh / client_fps` is to a whole number
4. Does the mode cover the client's resolution
5. Pixel count proximity to the client
6. Highest refresh rate

Refresh outranks resolution deliberately: downscaling 1080p to a Deck's 800p costs very little perceptually, while 60 fps versus 90 fps is felt immediately. Aspect ratio outranks pixel count because otherwise a 16:9 client can be handed a 21:9 host mode that merely happens to have a closer pixel count.

Cadence matters because Sunshine samples the scanout rather than the game's swapchain. A 119.88 Hz host feeding a 90 fps client cannot divide evenly, so every fourth frame repeats and motion judders at a steady 90 fps — visible, and untouchable by bitrate or codec. Without the cadence term a 45 fps client would take 119.88 Hz (2.66×) over 89.93 Hz (2×) purely because it is faster.

## Install

Requires KDE Plasma 6 (`kscreen-doctor`), PipeWire (`pactl`), Python 3.11+, and a dummy plug.

```sh
git clone https://github.com/guitaripod/remoteplay-display
cd remoteplay-display
./install.sh
```

That symlinks the script and the user service into place, seeds `~/.config/remoteplay-display/config.ini`, enables the service and runs `doctor`. Both are symlinks, so edits in the checkout are live with no reinstall step.

Then point `dummy` at your plug's connector. `doctor` lists every connector with its EDID product name, which usually makes it obvious:

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

`on` takes `--client WxH@FPS` to pick the mode for a specific client, and both `on` and `off` take `--owner NAME`.

The service handles Steam sessions automatically. Every layout carries an owner — `auto` for the daemon, `sunshine` for prep commands, `manual` otherwise — and `off --owner NAME` only restores a layout with that tag. So the daemon never unwinds a Sunshine session, Sunshine never unwinds a Steam one, and a bare `off` still forces everything back.

## Configuration

`~/.config/remoteplay-display/config.ini`

| key | default | meaning |
| --- | --- | --- |
| `dummy` | `HDMI-A-2` | connector name of the dummy plug |
| `mode` | `auto` | `auto`, or a pinned mode like `1920x1080@120` |
| `min_mode` | `1920x1080` | never pick a host mode smaller than this |
| `fallback_mode` | `1920x1080@120` | used when no recent client hint is available |
| `auto` | `true` | switch automatically on session start/end |
| `inhibit_idle` | `true` | hold an idle/sleep inhibitor during a session |
| `steam_log` | `~/.steam/steam/logs/streaming_log.txt` | where to read the client capture hint |
| `capture_hint_max_age` | `120` | seconds before a capture hint is considered stale |
| `settle_timeout` | `10.0` | seconds to wait for KWin to apply a layout |

## Dummy EDID

A dummy plug only offers the modes its EDID advertises, and those are chosen for looking like a generic monitor — not for feeding a Steam Deck at 90 Hz or an Apple TV at 4K60 RGB. Since nothing is actually receiving the signal, the EDID can simply be replaced with one that advertises the modes you need.

`dummy-edid` builds that EDID. `show` prints the timings and pipes the result through `edid-decode`:

```sh
./dummy-edid show
sudo ./install-edid.sh HDMI-A-2
```

The default timing set targets streaming clients rather than desktops:

| mode | clock | for |
| --- | --- | --- |
| 1920x1200@90 | 300.5 MHz | 16:10 handheld, preferred mode |
| 1280x800@90 | 131.3 MHz | Steam Deck, pixel-exact |
| 2560x1600@90 | 539.0 MHz | Steam Deck, supersampled |
| 3840x2160@60 | 533.0 MHz | 4K TV, RGB rather than YCbCr 4:2:0 |
| 2560x1440@120 | 497.3 MHz | high refresh tablet |
| 1920x1080@120 / @90 | 285.3 / 269.0 MHz | general |

Edit `TIMINGS` to change the set — entries are `cvt` modeline output. Note that CVT reduced blanking only supports refresh rates that are multiples of 60, so 90 Hz modes use plain CVT and cost more pixel clock.

`install-edid.sh` writes the blob to `/lib/firmware/edid/`, installs `dummy-edid` to `/usr/local/bin`, and enables `remoteplay-dummy-edid@<connector>.service`, which applies it **before `display-manager.service`**. That ordering is the point: the override is a `debugfs` write, and doing it while a compositor is running looks like a hotplug, which makes KWin re-lay-out every output. Applying it before the session starts avoids that entirely.

To try one without rebooting, `sudo ./dummy-edid install` writes it live and forces the hotplug — expect your outputs to be rearranged. `sudo ./dummy-edid clear` reverts, as does a reboot; the override is never persistent on its own.

Verify the kernel took it with `modetest -M nvidia-drm -c` (or your driver's name) rather than `kscreen-doctor`, which reports the compositor's cached list.

## Sunshine / Moonlight

Sunshine sessions never create Steam's null sink, so the daemon cannot see them. Sunshine announces them instead — add this to `~/.config/sunshine/sunshine.conf`:

```
global_prep_cmd = [{"do":"/home/you/.local/bin/remoteplay-display on --owner sunshine","undo":"/home/you/.local/bin/remoteplay-display off --owner sunshine"}]
output_name = 0
```

`do` runs before capture starts and `undo` after it stops, and Sunshine exports `SUNSHINE_CLIENT_WIDTH`, `SUNSHINE_CLIENT_HEIGHT` and `SUNSHINE_CLIENT_FPS` to both — which is exactly the hint the mode scorer wants, so Moonlight clients get per-client modes with no extra configuration.

`output_name = 0` matters: with KMS capture Sunshine addresses displays by index among the *enabled* outputs, and once `do` has run the dummy is the only one left. `doctor` warns if it is set to anything else.

### Steam games as Sunshine apps

`steam-app-launch <appid>` starts a Steam game and blocks until it exits, so Sunshine ends the session when you quit rather than stranding it on the desktop:

```json
{
    "name": "God of War Ragnarök",
    "cmd": "steam-app-launch 2322010",
    "auto-detach": false,
    "wait-all": true
}
```

It tracks `SteamLaunch AppId=<id>` on the reaper's command line, which is the one handle that survives every Proton launcher indirection.

This is also the workaround for games that bind input to whichever pad is "player one" — Sunshine's virtual gamepad exists before the app command runs, so the game finds a controller at startup. God of War Ragnarök needs exactly that: launching it *from* a Steam Remote Play client starts the game first and connects the controller seconds later, and the game then ignores it for the rest of the session.

## Design notes

**Atomic layout changes.** Every switch is a single `kscreen-doctor` invocation. Mid-transition KWin logs `There are no outputs - creating placeholder screen`; atomicity is the only reason that isn't a visible failure.

**Mode ids, not mode names.** A dummy plug commonly exposes several distinct modes sharing one name — one tested plug has two different modes both called `1920x1080@60`. Layouts are therefore snapshotted as `(width, height, refresh)` and re-resolved against the live mode list at apply time. This is load-bearing rather than theoretical: KWin renumbers mode ids whenever connectors re-enumerate, so a monitor power-cycle mid-session is enough to invalidate them.

**Verification, not fire-and-forget.** After applying, state is polled every 250ms until the expected outputs are enabled and the dummy's mode size matches, with one retry before failing loudly. Position and priority are deliberately not asserted, because KWin normalizes output geometry and asserting it produces false failures.

**Idle inhibition.** A detached child holds `org.freedesktop.ScreenSaver.Inhibit` plus a logind `idle:sleep` block for the duration of a session. Gamepad-only input may not reset KWin's idle timer, and a blanked dummy means a black stream with no local display to notice it on.

**Crash recovery.** State is flock'd JSON. A `SIGKILL` leaves it stale; the next start reconciles "marked active, owned by the daemon, but no session sink" and restores. Restore skips outputs that are no longer connected, and if nothing in the snapshot is connected it keeps the dummy enabled rather than leaving KWin with zero outputs.

**Idle cost.** No subprocesses. The watch loop sleeps until either a debounced sink event or a 5s safety tick.

## Known limits

**Do not add `-pipewire` to Steam's launch options on KDE Wayland**, even though Steam's own log suggests it. [steam-for-linux#13348](https://github.com/ValveSoftware/steam-for-linux/issues/13348) reports stale frames being injected into the stream via the PipeWire capture path on Plasma 6.7. Without it Steam captures games through its Vulkan layer, which is unaffected by any of this.

**Games Steam's Vulkan capture layer doesn't attach to** — typically emulators added as non-Steam shortcuts — fail with `k_ECaptureFailedReasonPipewireRequired` and stream nothing. That is a capture problem, not a display problem, and this tool does not address it.

**Dummy plugs lie about 4K.** Many advertise 4K60 but only offer it as YCbCr 4:2:0, with RGB capped at 4K30 by a 297 MHz TMDS limit. Linux does not expose 4:2:0-only modes, so the driver never offers 4K60. Check `doctor` for what your plug actually exposes — and if it comes up short, [replace its EDID](#dummy-edid) rather than the plug.

**Games with a saved render resolution do not follow the dummy.** A game that stored `3840x2160` from your desktop keeps rendering a 4K swapchain when the host switches to the dummy, and the capture layer grabs that oversized surface — so the stream shows a zoomed crop rather than the game. Switching between borderless and exclusive fullscreen does not help when the game exposes its own resolution setting, because that setting applies to both. Fix it in the game, while a session is running so the dummy's modes are the ones being offered, by setting the resolution to whatever `status` reports for the dummy. `min_mode` bounds how bad the mismatch can get, which is why it defaults to 1080p instead of letting the scorer pick a pixel-exact 1280x800.

**Steam cannot capture the desktop on KDE Wayland**, only games — its Vulkan layer attaches to a game, and everything else streams as `Desktop Black Frame`. So "connect to the host and browse the library" is a black screen there, and anything that needs a session running *before* a game launches has to go through Sunshine, which captures via KMS.
