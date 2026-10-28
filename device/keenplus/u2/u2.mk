# u2.mk -- KeenPlus U2 product definition (AOSP 8.1, 32-bit, low-RAM BT audio receiver).
#
# Strategy (plan section 15.1): build ADDITIVELY from the generic AOSP arm
# product and a curated package whitelist, rather than inheriting a fat
# full_* / MTK product and subtracting.  Telephony, camera, NFC, print and all
# MTK bloat are simply never added.

# Inherit the standard no-telephony AOSP base (full_base.mk).  Deliberately
# NOT aosp_arm/full (they drag in telephony, NFC -- whose libnfc-nci is the
# only consumer of AOSP libpower.so, which would fight the MTK libpower blob
# for /system/lib/libpower.so -- and goldfish/emulator files).
$(call inherit-product, $(SRC_TARGET_DIR)/product/full_base.mk)

# Core Android services we do want (framework, SystemUI, Settings, media, etc.).
$(call inherit-product, $(SRC_TARGET_DIR)/product/core.mk)

# Device-specific packages, blobs, copy-files and board properties.
$(call inherit-product, device/keenplus/u2/device.mk)

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
PRODUCT_NAME := u2
PRODUCT_DEVICE := u2
PRODUCT_BRAND := keenplus
PRODUCT_MODEL := U2
PRODUCT_MANUFACTURER := keenplus
PRODUCT_DEFAULT_DEVICENAME := KeenPlus U2

# The device shipped with API 24 (Android 7.0).  Keeping first_api_level=24 lets
# the 8.1 framework treat this as an upgraded device and relax Treble/VNDK
# requirements (non-Treble, decision D4).
PRODUCT_PROPERTY_OVERRIDES += \
    ro.board.first_api_level=24 \
    ro.treble.enabled=false \
    ro.board.platform=mt6735m

# 32-bit-only userspace (decision D2): single zygote, ramdisk ships only
# init.zygote32.rc.
PRODUCT_PROPERTY_OVERRIDES += \
    ro.zygote=zygote32 \
    ro.product.cpu.abilist=armeabi-v7a,armeabi \
    ro.product.cpu.abilist32=armeabi-v7a,armeabi \
    ro.product.cpu.abilist64=

# --------------------------------------------------------------------------- #
# Low-RAM tuning (plan section 15.2) -- serves R3..R7
# --------------------------------------------------------------------------- #
PRODUCT_PROPERTY_OVERRIDES += \
    ro.config.low_ram=true \
    ro.lmk.critical=0 \
    ro.lmk.critical_upgrade=false \
    ro.lmk.upgrade_pressure=100 \
    ro.lmk.downgrade_pressure=100 \
    ro.config.max_starting_bg=2 \
    ro.sys.fw.bg_apps_limit=4 \
    ro.sys.fw.use_trim_settings=true \
    ro.sys.fw.trim_empty_percent=100 \
    ro.sys.fw.trim_cache_percent=100 \
    ro.sys.fw.empty_app_percent=50

# LMK thresholds for 1.5 GB RAM + 768 MiB zram (units: 4 KiB pages).
PRODUCT_PROPERTY_OVERRIDES += \
    ro.lmk.low=18432 \
    ro.lmk.low_minfree=18432 \
    ro.lmk.medium=24576 \
    ro.lmk.critical_minfree=36864

# ART / dexopt: trade first-run speed for lower resident memory.
PRODUCT_PROPERTY_OVERRIDES += \
    dalvik.vm.heapstartsize=1m \
    dalvik.vm.heapgrowthlimit=96m \
    dalvik.vm.heapsize=256m \
    dalvik.vm.heaptargetutilization=0.75 \
    dalvik.vm.heapminfree=512k \
    dalvik.vm.heapmaxfree=8m \
    dalvik.vm.usejit=false \
    dalvik.vm.usejitprofiles=false \
    dalvik.vm.dex2oat-Xms=64m \
    dalvik.vm.dex2oat-Xmx=256m \
    dalvik.vm.image-dex2oat-Xms=48m \
    dalvik.vm.image-dex2oat-Xmx=48m \
    dalvik.vm.dexopt.secondary=false \
    dalvik.vm.appimageformat= \
    pm.dexopt.first-boot=quicken \
    pm.dexopt.boot=verify \
    pm.dexopt.install=quicken \
    pm.dexopt.bg-dexopt=speed-profile \
    pm.dexopt.inactive=verify \
    pm.dexopt.shared=quicken

# --------------------------------------------------------------------------- #
# Disabled subsystems (plan sections 9 and 15.1) -- serves R6/R9
# --------------------------------------------------------------------------- #
PRODUCT_PROPERTY_OVERRIDES += \
    ro.radio.noril=yes \
    config.disable_camera=true \
    config.disable_print=true \
    config.disable_telephony=true \
    config.disable_consumerir=true \
    config.disable_networkstats=false \
    config.disable_atlas=true

# --------------------------------------------------------------------------- #
# Logging / tombstones (shipping build)
# --------------------------------------------------------------------------- #
PRODUCT_PROPERTY_OVERRIDES += \
    persist.log.tag=S \
    ro.logd.size=64K \
    debug.atrace.tags.enableflags=0 \
    ro.tombstoned.max_tombstone_count=3

# --------------------------------------------------------------------------- #
# Timezone / locale (match baseline)
# --------------------------------------------------------------------------- #
PRODUCT_PROPERTY_OVERRIDES += \
    persist.sys.timezone=Asia/Shanghai

# Build fingerprint / description are derived at build time; keep the model
# string aligned with the baseline so apps that gate on ro.product.model behave.
PRODUCT_BUILD_PROP_OVERRIDES += \
    BUILD_FINGERPRINT=keenplus/u2/u2:8.1.0/OPM8.190628.002/$(BUILD_NUMBER_UTCTIME):user/release-keys \
    PRIVATE_BUILD_DESC="u2-user 8.1.0 OPM8.190628.002 release-keys"
