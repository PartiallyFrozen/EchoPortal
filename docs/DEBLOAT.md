# Removing the rest of the OS

Optional. EchoPortal is the launcher, so none of these are visible anyway, but on a 1 GB device
their background services, sync jobs and receivers cost real memory. Removing them per-user is
reversible and leaves the system image untouched.

```bash
adb shell pm uninstall -k --user 0 <package>       # remove for the current user
adb shell cmd package install-existing <package>   # put it back
```

## What this project removed

LineageOS apps: `org.lineageos.jelly`, `org.lineageos.eleven`, `org.lineageos.etar`,
`org.lineageos.recorder`, `org.lineageos.audiofx`, `org.lineageos.backgrounds`,
`org.lineageos.profiles`, `org.lineageos.updater`.

AOSP apps: `com.android.calculator2`, `com.android.camera2`, `com.android.gallery3d`,
`com.android.documentsui`, `com.android.htmlviewer`, `com.android.contacts`, `com.android.egg`,
`com.android.simappdialog`, `com.android.bookmarkprovider`, `com.android.providers.calendar`,
`com.android.mms.service`, `com.android.bluetoothmidiservice`, `com.android.traceur`,
`com.android.dynsystem`, `com.android.systemui.plugin.globalactions.wallet`.

Wallpapers and screensavers: `com.android.wallpaper`, `com.android.wallpaper.livepicker`,
`com.android.wallpaperbackup`, `com.android.wallpapercropper`, `com.android.dreams.basic`,
`com.android.dreams.phototable`.

Printing: `com.android.printspooler`, `com.android.printservice.recommendation`, `com.android.bips`.

Backup: `com.stevesoltys.seedvault`, `org.calyxos.backup.contacts`.

## Keep these

- **`com.android.providers.contacts`.** Removing it crashes the Bluetooth phonebook service on boot,
  even though Bluetooth does not work on this device anyway.
- **Settings** (`com.android.settings`), which the shell's menu opens for Wi-Fi and developer
  options.
- **The clock app** (`com.android.deskclock`) if you want alarms; the shell can open it.

Nothing here touches `/system`, so a factory reset brings every package back.
