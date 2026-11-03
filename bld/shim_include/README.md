# shim_include —— 只为编译期补齐 NDK sysroot 缺失的个别内核 UAPI 头

背景：我们用 Android NDK r21e 的 sysroot（API 24）当 bionic 头文件来源，
但 NDK 精简掉了少数平台内部才会用到的头，其中 `linux/binder.h` 是
`frameworks/native/include/binder/Parcel.h` 的传递依赖
（btif_avrcp_audio_track.cpp → media/AudioTrack.h → AudioSystem.h → AudioPolicy.h → binder/Parcel.h）。

做法：从 AOSP `platform/bionic @ android-7.0.0_r36` 的
`libc/kernel/uapi/linux/binder.h` **原样复制一份**放在这里，
并把这个目录放在 include 搜索顺序的**最后**，避免遮蔽 NDK sysroot 里的同名头。

严禁：往这里塞业务代码或用新版内核头覆盖旧版（会改变结构体布局 → ABI 事故）。
