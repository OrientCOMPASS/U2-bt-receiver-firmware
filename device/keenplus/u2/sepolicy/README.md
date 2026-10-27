# device/keenplus/u2/sepolicy/

Baseline SEPolicy for the KeenPlus U2 AOSP 8.1 port (plan section 20 / decision
D6).  This is a **starter set**, not a complete policy: the final `.te` rules can
only be settled once the device is booted and `avc: denied` messages are captured
from a real build (this repo cannot build AOSP in CI -- see patches/README.md).

## What is here

- `device.te` -- declares the device-specific type and attributes.
- `file.te` -- custom file/label types (persist data for the BT audio glue).
- `u2_btaudio.te` -- domain for the small native helper that bridges the A2DP
  sink HAL to the platform audio path (started from init.rc).
- `init.te` -- extra init permissions needed on this platform.
- `property.te` -- custom property types.
- `file_contexts` -- maps paths to the types above.
- `property_contexts` -- maps `persist.sys.u2.*` properties to their types.
- `service_contexts` -- maps any custom binder services.
- `genfs_contexts` -- labels proc/sysfs nodes this device touches.

## How to use

```
BOARD_SEPOLICY_DIRS += device/keenplus/u2/sepolicy
```

(Already wired in `device/keenplus/u2/BoardConfig.mk`.)

## Iteration workflow (on a real build, not CI)

1. Boot the image, reproduce the BT/audio path.
2. `dmesg | grep avc` and `adb logcat -b events | grep avc`.
3. Run `audit2allow -i <avc.log>` to generate candidate rules.
4. **Review every rule** -- never blanket-allow; keep domains minimal.
5. Fold approved rules back into the `.te` files here.

The stubs below are intentionally conservative: they declare types and only the
minimum obviously-safe permissions so the build does not fail on undefined types.
