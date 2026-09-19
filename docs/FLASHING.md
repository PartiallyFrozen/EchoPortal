# Flashing an Echo Spot

This replaces Amazon's software with LineageOS 18.1 (Android 11) and installs EchoPortal as the
launcher. It erases the device, removes it from your Amazon account's control, and cannot be
undone without the stock images. Your device, your risk.

Applies only to the **first-generation Echo Spot** (`rook`, model VN94DQ). See
[HARDWARE.md](HARDWARE.md).

## What you need

- The Spot, its power supply, and a USB-A to micro-USB data cable.
- A Windows PC (the unlock tooling is easiest there) or a Linux box.
- [amonet-rook](https://github.com/chaosmaster/amonet) v2.0.0 or later, the bootrom exploit that
  unlocks the bootloader.
- TWRP 3.7.0 for `rook`, and a LineageOS 18.1 `rook` build (userdebug).
- `adb` and `fastboot` from the Android platform-tools.

None of these are redistributed here. Download them from their own projects.

## 1. Unlock the bootloader

The MT8163 bootrom accepts commands for a moment at power-on, which is what amonet uses. On
Windows this needs the MediaTek preloader driver, and Windows will complain that it is unsigned.

```bash
# Spot unplugged. Start the script, then plug the cable in.
cd amonet-rook
./fastbrick.bat          # Windows;  ./bootrom-step.sh on Linux
```

The device appears as *MT65xx Preloader* (`VID_0E8D PID_2000`) for about a second at power-on. If
the script does not see it, unplug, run the script first, then plug in. When it finishes, the Spot
reboots into an unlocked bootloader.

## 2. Install TWRP and LineageOS

```bash
fastboot flash recovery twrp-3.7.0-rook.img
fastboot reboot recovery

# In TWRP: Wipe -> Format Data, then
adb sideload lineage-18.1-rook.zip
```

Reboot. You now have Android 11 with no Amazon software on it, and a Trebuchet launcher you are
about to replace.

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

Long-press the screen → *PC agent address* → `ws://<your-pc-ip>:8765`.

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
