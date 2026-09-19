# The hardware

EchoPortal targets one device: the **first-generation Amazon Echo Spot** (2017), model **VN94DQ**,
codename **`rook`**. Later Echo Show and Echo Dot with clock models are different hardware and are
not supported.

| | |
| --- | --- |
| SoC | MediaTek MT8163 (4x Cortex-A53, 1.3 GHz) |
| RAM | 1 GB |
| Display | 2.5" round LCD, 480 x 480 |
| Radios | Wi-Fi 802.11 a/b/g/n/ac, Bluetooth 4.2 |
| Audio | 1.4" speaker, 4-mic array |
| Camera | VGA (non-functional under LineageOS) |

## Things worth knowing before you build for it

- **The panel is round but the framebuffer is square.** Anything drawn in the corners is invisible.
  The visible glass is also slightly offset from the framebuffer, which is why the shell has a
  calibration step (long-press → *Calibrate screen*); the defaults are dx -2, dy +26, scale 1.07,
  and the bottom ~40 px of the framebuffer sit behind the bezel.
- **The mute button is the power button** as far as Android is concerned.
- **The mic array is quiet.** Captured audio sits around -34 dBFS, roughly 30 dB below a phone, so
  the voice face normalises the signal on the PC before transcribing.
- **Bluetooth does not work** under LineageOS. The Broadcom firmware never answers the HCI
  `Read Encryption Key Size` command the Android stack expects, so pairing fails after connecting.
  Rebuilding the vendor library did not fix it. Wi-Fi is the only usable radio.
- **The camera does not work** either, and no driver exists. Any feature needing a camera is out.
- **It is slow.** One A53 core is doing your drawing. Keep per-frame work trivial, cache anything
  you can, and let the PC do the parsing, polling and transcoding.
- **Storage is fine.** 5 GB of `/data`, of which a clean install with EchoPortal uses well under
  half a gigabyte.

## Why this device

It cost four dollars at a thrift store. Amazon stopped shipping meaningful updates for it, the
stock software cannot be repurposed, and the bootloader unlock is a bootrom exploit that cannot be
patched in software, so any unit you find can be flashed. A round 480 px screen with a speaker and
a decent mic array is a good dashboard and a terrible paperweight.
