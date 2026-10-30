# rootexec v4 + Bluetooth_sink_v3.apk — system-APK swap route (WORKING)

## Why the /data update route failed (two independent blockers)
1. **versionCode tie** (v2): PMS boot scan logged
   `reverting from /data/app/com.android.bluetooth-1: new version 24 better than installed 24`
   -> equal version = revert + delete update. Fixed in v3 by patching AXML
   versionCode 24->25 (scripts/patch_vc.py).
2. **linker namespace** (v3 on /data): updated-system-app processes do NOT get
   /system/lib in their classloader namespace; libbluetooth_jni.so NEEDS
   libandroid_runtime.so (not a public library) ->
   `UnsatisfiedLinkError: dlopen failed: library "libandroid_runtime.so" not found`
   -> AdapterService crashed twice, AMS gave up.

## Working route: swap the APK inside /system (rootexec v4)
- `pm uninstall com.android.bluetooth` (remove any /data update first!)
- push Bluetooth_sink_v3.apk -> /data/local/tmp/bt_new_apk.apk
- rootexec v4 (md5-gated, idempotent): backup stock APK to
  /data/local/tmp/Bluetooth_stock_backup.apk, `rm -rf /system/app/Bluetooth/oat`,
  copy new APK to /system/app/Bluetooth/Bluetooth.apk (644 root:root,
  restorecon u:object_r:system_file:s0), remount ro.
- Reboot: PMS rescans system APK (vc25 > recorded 24 -> accepted), dex2oat on
  first boot, BT starts as bundled system app -> JNI resolves via system
  namespace.

## Verified on device (2026-10-30)
- pm path: /system/app/Bluetooth/Bluetooth.apk, versionCode=25, process alive
- dumpsys bluetooth_manager: enabled=true, state=STATE_ON,
  address 00:00:46:03:35:01, name "KeenPlus U2"
- Profiles: A2dpService, **A2dpSinkService**, **AvrcpControllerService** (+stock set)
- BT stack .so: /system/lib/hw/bluetooth.default.so = sink stack
  (md5 c561b6128e9293bde3280f7aca3d5b17), survives reboots.

## Artifacts
- Bluetooth_sink_v3.apk md5 41d711dc6e2060ee517a54247ccf665f (release
  firmware-btsink-apk-v3-20261030): platform-signed (cert SHA-1
  27196e386b875e76adf700e7ea84e4c6eee33dfa), classes_fixed.dex, arsc
  a2dp_sink+avrcp_controller=true, versionCode 25, bundled
  lib/armeabi-v7a/libbluetooth_jni.so (STORED).
- rootexec.sh v4 md5 89b1a0be50df8e8d5b156e9367183fa3.
- Rollback: restore /data/local/tmp/Bluetooth_stock_backup.apk over
  /system/app/Bluetooth/Bluetooth.apk (+ bt_stock_backup.so for the .so), reboot.
