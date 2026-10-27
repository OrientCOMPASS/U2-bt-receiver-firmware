# kernel/ -- reused kernel strategy (plan section 13 / decision D3)

The KeenPlus U2 firmware runs AOSP 8.1 userspace on top of the **stock MT6735M
Linux 3.18.35 kernel** taken from the Android 7.0 baseline.  We do **not** write a
new kernel or a new board bring-up.  Concretely:

- **Kernel image + DTB come from the baseline.**  The boot image `kernel` field is
  `Image.gz + appended DTB` (63,240 B, FDT v17).  The appended DTB already
  contains everything this port needs:
  - `panel st7701s_fw_dsi_vbo` timing (porch/clk/lane)
  - `i2s1` GPIO pin groups
  - `msdc0..msdc3` controllers
  - `mt6311`/`mt6328` regulators
  - `keypad`, `usb0_base`, `bat_meter`
- **dt_size stays 0 in the boot header**, because the DTB is appended to the image
  (not passed as a separate second stage).  `mkbootimg` must therefore be invoked
  with the concatenated `Image.gz u2.dtb` and `--dt_size 0`.
- **Only config additions when needed.**  If AOSP 8.1 userspace needs a kernel
  option the baseline lacks, add it via the fragment and rebuild with
  `ARCH=arm64` + `gcc 4.9` (matching the baseline toolchain) so the ABI stays
  compatible with the baseline modules.

## Files

- `u2_8.1_config.fragment` -- the list of CONFIG_ options AOSP 8.1 requires
  (cgroups/memcg, namespaces, binder/ashmem/ion/sync, zram-lz4, netfilter,
  instrumentation, seccomp/audit).  These are *requirements to verify*, not a full
  defconfig.
- `check-kernel-config.py <.config>` -- audits a real kernel `.config` against that
  fragment and exits non-zero if anything required is missing/mismatched.

## Why not a custom kernel?

Decision D3 in the plan explicitly forbids a custom kernel build for this project.
A 3.18.35 kernel already ships the MT6735M drivers, and the ABI matches the
baseline prebuilt vendor modules.  Rebuilding only to add a config flag risks a
module ABI mismatch that would not boot; reusing the baseline image is the
lower-risk path.  The full AOSP build (not run in CI) is what would produce the
final boot.img; this directory documents and machine-checks the kernel contract.
