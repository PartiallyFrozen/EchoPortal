# Flashing an Echo Spot

This replaces Amazon's software with LineageOS 18.1 (Android 11) and installs EchoPortal as the
launcher. It erases the device, removes it from your Amazon account's control, and cannot be
undone without the stock images. Your device, your risk.

Applies only to the **first-generation Echo Spot** (`rook`, model VN94DQ). See
[HARDWARE.md](HARDWARE.md).

## What you need

- The Spot, its power supply, and a USB-A to micro-USB data cable.
- A Windows PC (the unlock tooling is easiest there) or a Linux box.
- [amonet-rook v2.0.0](https://xdaforums.com/attachments/amonet-rook-v2-0-0-zip.6374234/) from the
  [unlock thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-spot-2017-rook.4754878/).
  Downloading the attachment requires an XDA account. Do **not** build this from a source clone:
  clones omit the compiled payloads. The ZIP includes TWRP and a patched Windows `fastboot.exe`.
- A [LineageOS 18.1 `rook` build](https://github.com/amazon-oss/releases/releases/tag/lineage-18.1-rook-v0.3).
  The current asset is `lineage-18.1-20251108-UNOFFICIAL-rook.zip` (SHA-256
  `2755428c124df88ffd3bc6a9c36db16bbe3b7e639883fd1092dc839ac5ae9d13`).
- `adb` and `fastboot` from the Android platform-tools.

None of these are redistributed here. Download them from their own projects. On this PC the
`flash-rook\` folder already contains the verified LineageOS ZIP, the built APK, and four scripts:
`1-unlock.cmd`, `2-sideload-lineage.cmd`, `3-install-echoportal.cmd`, and `4-verify.cmd`. The staged
`amonet-rook-v2.0.0.zip` is the real XDA attachment (42,696,815 bytes) and contains the compiled
payloads.

## 1. Unlock the bootloader

**Risk:** this can permanently brick the device. Use the included power supply and a *data* USB-A to
micro-USB cable. Do not interrupt the exploit once it starts. Never flash `preloader`, `LK`, `TZ`,
or `TEE1`.

On stock FireOS, Developer options expose only **Ship Mode**. USB debugging is not available until
the bootloader is unlocked.

```bat
:: Extract amonet-rook-v2.0.0.zip next to these docs, then:
cd amonet
fastbrick.bat
```

The script asks you to put the Spot into fastboot mode:

1. Leave the Spot unplugged from the PC and connected to its AC charger.
2. Power it off.
3. Hold **Volume Up + Volume Down + Mute** together until `=> FASTBOOT mode...` appears.
4. Connect the micro-USB data cable to the PC while still holding the buttons.

The script detects `ROOK`, checks that the bootloader is not already unlocked, picks the payload for
your FireOS build, and asks for confirmation. Type `YES` exactly. It may take up to five minutes and
the device will reboot into TWRP when done. If Windows reports error 28 for `VID_0BB4 PID_0C01`,
install the Kindle Fire driver (staged as `flash-rook\kindle_fire_usb_driver.zip` here). If it shows
*MT65xx Preloader*, install a WinUSB driver for `VID_0E8D PID_2000` rather than flashing anything.

## 2. Install LineageOS

TWRP is already installed by amonet; there is no separate recovery image to flash.

```bash
# In TWRP: Wipe -> Advanced Wipe -> Dalvik/ART Cache, System, Data, Cache -> Swipe.
# Then: Advanced -> ADB Sideload -> swipe to start.
adb sideload lineage-18.1-20251108-UNOFFICIAL-rook.zip
```

Reboot to system. You now have Android 11 with no Amazon software on it, and a Trebuchet launcher
you are about to replace. On this ROM the **Mute** button is also the power button; hold **Volume
Up** while applying power to return to TWRP.

## 3. Turn on ADB over Wi-Fi

Settings → About → tap the build number seven times → Developer options:

- **USB debugging**: on
- **Rooted debugging**: on (the shell needs it for a few settings)
- **ADB over network**: on, note the port (5555)

```bash
adb connect <spot-ip>:5555
```

Accept the key prompt on the device. From here the cable is optional.

## 4. Install EchoPortal

```bash
adb install -r EchoPortal-<version>-debug.apk
adb shell cmd package set-home-activity com.echoportal/.MainActivity
```

Then a few settings that make it behave like an appliance rather than a phone:

```bash
adb shell settings put system screen_off_timeout 2147483647
adb shell settings put global stay_on_while_plugged_in 3
adb shell locksettings set-disabled true
```

Long-press the screen → *PC agent address* → `ws://<your-pc-ip>:8765`. On this PC,
`flash-rook\3-install-echoportal.cmd` does all of the above, including writing the `api_token` from
the installed agent config into the Spot preferences so the token dialog is not needed.

## 5. Optional: remove the rest of the OS

EchoPortal is the launcher, so nothing else is visible, but the stock apps still run background
services on a 1 GB device. Removing them per-user is reversible:

```bash
# Examples; the full list this project used is in docs/DEBLOAT.md
adb shell pm uninstall -k --user 0 org.lineageos.jelly
adb shell pm uninstall -k --user 0 org.lineageos.eleven

# To restore any of them:
adb shell cmd package install-existing <package>
```

Keep `com.android.providers.contacts`: the Bluetooth phonebook service crashes without it, even
though Bluetooth itself does not work.

## Going back

Only if you took backups before flashing, with `dd` from TWRP over the whole partition layout.
Without them there is no route back to the stock software. Take the backups first.
