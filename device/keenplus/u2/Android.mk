# Android.mk -- device-level build rules for KeenPlus u2.
#
# AOSP 8.1's core build system has NO TARGET_PREBUILT_KERNEL support:
# build/core/* defines INSTALLED_KERNEL_TARGET := $(PRODUCT_OUT)/kernel and
# passes it to mkbootimg, but nothing ever creates the file (verified:
# zero references to *PREBUILT_KERNEL under build/core, and the generic
# product's out/ has no kernel node either -- Lineage-style trees patch
# this in, we replicate the missing edge here instead).
#
# This file is parsed by kati after product config, so PRODUCT_OUT and
# TARGET_PREBUILT_KERNEL (from BoardConfig.mk) are both already defined.
# The rule below supplies the ninja edge
#   out/target/product/u2/kernel <- device/keenplus/u2/prebuilt/kernel
# that boot.img is missing.

LOCAL_PATH := $(call my-dir)

ifneq ($(strip $(TARGET_PREBUILT_KERNEL)),)
$(PRODUCT_OUT)/kernel: $(TARGET_PREBUILT_KERNEL)
	@echo "prebuilt kernel: $< -> $@"
	$(hide) mkdir -p $(dir $@)
	$(hide) cp -f $< $@
endif

# ---------------------------------------------------------------------------
# Mali EGL/GLES loader aliases.
#
# The baseline /vendor/lib/egl contains ONLY egl.cfg ("0 1 mali") and the
# single ARM DDK blob libGLES_mali.so (25.7 MB, ships EGL + GLESv1_CM +
# GLESv2 in one file).  The 7.0 EGL loader dlopens libEGL_mali.so /
# libGLESv1_CM_mali.so / libGLESv2_mali.so by name; the stock device had
# those as symlinks created at first boot, so they are absent from the
# system image.  Recreate them as real symlinks inside the built image
# (three 25 MB copies would blow the /system size budget R1).
# The libGLES_mali prebuilt module itself comes from the generated
# vendor/keenplus/u2/Android.mk (extracted blob).
# ---------------------------------------------------------------------------
U2_MALI_REAL := $(TARGET_OUT_VENDOR)/lib/egl/libGLES_mali.so
U2_EGL_ALIASES := $(addprefix $(TARGET_OUT_VENDOR)/lib/egl/,\
    libEGL_mali.so libGLESv1_CM_mali.so libGLESv2_mali.so)

$(U2_EGL_ALIASES): $(U2_MALI_REAL)
	@echo "mali EGL alias symlink: $@"
	$(hide) ln -sf libGLES_mali.so $@

ALL_DEFAULT_INSTALLED_MODULES += $(U2_EGL_ALIASES)
