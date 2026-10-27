# Resource overlays

Build-time overlays merged via `DEVICE_PACKAGE_OVERLAYS += device/keenplus/u2/overlay`.

- `packages/apps/Bluetooth/res/values/config.xml` — enables A2DP Sink + AVRCP
  Controller, keeps A2DP Source, and disables every profile this product does not
  use (plan section 16.2).  Only resource names that exist in AOSP 8.1
  `packages/apps/Bluetooth` are overridden.

Add further overlays (e.g. `frameworks/base/core/res/res/values/config.xml`) here
as needed; verify each key exists in android-8.1.0_r80 before adding it.
