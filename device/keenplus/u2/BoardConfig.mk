# BoardConfig.mk -- KeenPlus U2 (MT6735M / tek6735m_65_d_n)
#
# Every numeric value below is taken from the measured baseline firmware
# (B04_35_1.5_8_3inch_M304_ST7701SN_20251016) and cross-checked against
# docs/DEVELOPMENT_PLAN.md sections 3, 4 and 14.2.  Values that still need to be
# confirmed from the device DTB / logo.bin are marked with "TODO(confirm)".
#
# Target: self-built AOSP 8.1 (android-8.1.0_r80), 32-bit-only userspace on the
# stock 64-bit 3.18.35 kernel, non-Treble, legacy MTK HALs reused via passthrough.

# --------------------------------------------------------------------------- #
# Architecture: 32-bit single-arch (decision D2)
# --------------------------------------------------------------------------- #
TARGET_ARCH := arm
TARGET_ARCH_VARIANT := armv7-a-neon
TARGET_CPU_VARIANT := cortex-a53
TARGET_CPU_ABI := armeabi-v7a
TARGET_CPU_ABI2 := armeabi
TARGET_CPU_SMP := true
# No TARGET_2ND_ARCH -> ro.product.cpu.abilist=armeabi-v7a,armeabi
# ro.zygote=zygote32 (ramdisk ships only init.zygote32.rc).
# NOTE: the kernel stays arm64 (bootopt=64S3,32N2,64N2 is unchanged); only
# userspace is 32-bit.

TARGET_BOARD_PLATFORM := mt6735m
TARGET_NO_BOOTLOADER := true
BOARD_USES_MTK_HARDWARE := true

# --------------------------------------------------------------------------- #
# Kernel: self-built prebuilt (plan section 13)
# --------------------------------------------------------------------------- #
TARGET_PREBUILT_KERNEL := device/keenplus/u2/prebuilt/kernel
# v1 bring-up: permissive until the MTK vendor services (wmt_loader,
# wmt_launcher, nvram_daemon, fuelgauged -- unlabelled 7.0 binaries running
# through the linker64 shim) have their own domains.  Shipping builds MUST
# re-enable enforcing and add policy under device/keenplus/u2/sepolicy/.
BOARD_KERNEL_CMDLINE := bootopt=64S3,32N2,64N2 androidboot.veritymode=disabled androidboot.selinux=permissive
BOARD_KERNEL_PAGESIZE := 2048
BOARD_KERNEL_BASE := 0x40080000
# offset semantics: mkbootimg computes addr = base + offset.  The baseline header
# stores kernel_addr 0x40080000 / ramdisk_addr 0x44000000 / second_addr
# 0x40F00000 / tags_addr 0x4E000000; with base = kernel_addr the four offsets are
# exactly the values below (verified against the baseline boot.img header).
BOARD_MKBOOTIMG_ARGS := --kernel_offset 0x00000000 \
                        --ramdisk_offset 0x03f80000 \
                        --second_offset 0x00e80000 \
                        --tags_offset 0x0df80000
BOARD_BOOT_HEADER_VERSION := 0
# The baseline appends the DTB to Image.gz (dt_size in the header is 0).  Keep
# that layout: cat Image.gz u2.dtb > kernel, header dt_size = 0.
BOARD_USES_LEGACY_MKBOOTIMG := true

# --------------------------------------------------------------------------- #
# Partitions: reuse the baseline 26-partition layout, do NOT touch the GPT (D4)
# --------------------------------------------------------------------------- #
# DECIMAL values only: make_ext4fs parses `-l` with a base-10 strtoull and
# chokes on 0x... ("filesystem size too small"); inline comments are also
# banned here because make keeps the trailing whitespace in the value.
# boot / recovery: 16 MiB
BOARD_BOOTIMAGE_PARTITION_SIZE     := 16777216
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 16777216
# system: 2952 MiB (0xB8800000)
BOARD_SYSTEMIMAGE_PARTITION_SIZE   := 3095396352
# userdata: 3 GiB (0xC0000000)
BOARD_USERDATAIMAGE_PARTITION_SIZE := 3221225472
# cache: 400 MiB (0x19000000)
BOARD_CACHEIMAGE_PARTITION_SIZE    := 419430400
# scatter block_size 128 KiB (0x20000)
BOARD_FLASH_BLOCK_SIZE             := 131072
TARGET_USERIMAGES_USE_EXT4 := true
BOARD_CACHEIMAGE_FILE_SYSTEM_TYPE := ext4
# save space:
BOARD_SYSTEMIMAGE_JOURNAL_SIZE := 0
# Goal: actual /system content <= 620 MiB (R1).  A small inode count keeps the
# ext4 metadata overhead down for a trimmed system.
BOARD_SYSTEMIMAGE_EXT4_INODE_COUNT := 65536

# --------------------------------------------------------------------------- #
# Non-Treble (D4)
# --------------------------------------------------------------------------- #
# vendor lives under system/vendor:
TARGET_NO_VENDOR_IMAGE := true
BOARD_VNDK_VERSION :=                                # empty == non-Treble

# --------------------------------------------------------------------------- #
# Disable dm-verity and full-disk encryption (D5)
# --------------------------------------------------------------------------- #
BOARD_VERITY_DISABLE := true
TARGET_HW_DISK_ENCRYPTION := false

# --------------------------------------------------------------------------- #
# Graphics: legacy HAL + HWC2 passthrough (D3)
# --------------------------------------------------------------------------- #
TARGET_USES_HWC2 := true
# 3 buffers waste ~8 MiB/frame; 2 is enough for a 3" panel:
NUM_FRAMEBUFFER_SURFACE_BUFFERS := 2
TARGET_FORCE_HWC_FOR_VIRTUAL_DISPLAYS := false
TARGET_RUNNING_WITHOUT_SYNC_FRAMEWORK := false
BOARD_HAS_FLIPPED_SCREEN := false
MAX_VIRTUAL_DISPLAY_DIMENSION := 0

# --------------------------------------------------------------------------- #
# Bluetooth (legacy bt_vendor_interface_t; 8.1 does not use HIDL for BT)
# --------------------------------------------------------------------------- #
BOARD_HAVE_BLUETOOTH := true
BOARD_HAVE_BLUETOOTH_MTK := true
BOARD_BLUETOOTH_BDROID_BUILDCFG_INCLUDE_DIR := device/keenplus/u2
# save power (plan section 16.6):
BOARD_BLUETOOTH_BDROID_HCILP_INCLUDED := 0

# --------------------------------------------------------------------------- #
# Audio (MTK tinyalsa path)
# --------------------------------------------------------------------------- #
BOARD_USES_GENERIC_AUDIO := false
BOARD_USES_ALSA_AUDIO := false

# --------------------------------------------------------------------------- #
# SELinux
# --------------------------------------------------------------------------- #
# The v0 starter sepolicy (u2_btaudio helper domain) was removed: it tripped
# 8.1 Treble neverallow checks (nonplat domains may not use their own exec
# entrypoints / servicemanager binder), and the helper itself is obsolete --
# v1 runs the patched AOSP Bluetooth stack (bluetooth.default.so + APK).
# v1 bring-up runs permissive (see BOARD_KERNEL_CMDLINE); a shipping build
# must add a compliant device policy and re-enable enforcing.

# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #
TARGET_NO_RECOVERY := false
# no modem (non-goal, plan section 9):
TARGET_NO_RADIOIMAGE := true
BOARD_HAS_NO_SELECT_BUTTON := true
TARGET_RECOVERY_PIXEL_FORMAT := "RGBA_8888"

# Panel: 3.0" ST7701SN, DSI video mode (kernel LCM driver st7701s_fw_dsi_vdo_boe).
# TODO(confirm): derive the exact resolution from the baseline DTB lcm_params-dsi-*
# properties or from logo.bin dimensions (plan Appendix C item 6).  The values
# below are a sane ST7701SN default and MUST be confirmed before first boot.
TARGET_SCREEN_WIDTH  := 480
TARGET_SCREEN_HEIGHT := 800
