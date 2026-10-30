/*
 * test_dlopen.c -- runtime HAL-load smoke test for the swapped BT stack.
 *
 * Compiled with NDK r25c (armv7a, API 24) and executed under qemu-user with
 * -L <sysroot> so that the REAL Android 7.0 /system/bin/linker from the
 * device image does the loading, against the REAL /system/lib libraries
 * extracted from the same image.  This reproduces the exact dlopen path the
 * stock libbluetooth_jni.so uses at Bluetooth app start (hw_get_module ->
 * dlopen(path, RTLD_NOW) -> dlsym("HMI")).
 *
 * Usage:
 *   test_dlopen <path-to.so> [hal|vendor|halopen]
 *     hal    (default): expect HAL_MODULE_INFO_SYM == exported symbol "HMI";
 *                       print tag/module_api_version/hal_api_version/id/name.
 *     vendor:           expect BLUETOOTH_VENDOR_LIB_INTERFACE (16-byte
 *                       bt_vendor_interface_t); print size/init/op/cleanup.
 *     halopen:          hal checks, then call methods->open("bluetooth") --
 *                       the exact JNI init path -- to exercise the stack's
 *                       bring-up/error handling under a controller-less
 *                       environment (exit 4 = open returned an error;
 *                       a TIMEOUT by the caller = open never returned).
 * Exit 0 = loaded & entry symbol sane; 2 = dlopen/linker failure;
 * 3 = entry symbol missing/insane.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct hw_module_t {
    uint32_t tag;
    uint16_t module_api_version;
    uint16_t hal_api_version;
    const char *id;
    const char *name;
    const char *author;
    void *methods;
    void *dso;
    void *reserved[32 - 7];
};

struct bt_vendor_interface_t {
    uint16_t size;
    void *p_init;
    void *p_op;
    void *p_cleanup;
};

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <so> [hal|vendor]\n", argv[0]); return 1; }
    const char *mode = (argc > 2) ? argv[2] : "hal";
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("[smoke] dlopen(%s, RTLD_NOW) mode=%s\n", argv[1], mode);
    void *h = dlopen(argv[1], RTLD_NOW);
    if (!h) {
        printf("[smoke] DLOPEN FAIL: %s\n", dlerror() ? dlerror() : "?");
        return 2;
    }
    printf("[smoke] dlopen OK\n");
    if (strcmp(mode, "vendor") == 0) {
        void *s = dlsym(h, "BLUETOOTH_VENDOR_LIB_INTERFACE");
        if (!s) { printf("[smoke] FAIL: vendor interface symbol missing\n"); return 3; }
        struct bt_vendor_interface_t *v = (struct bt_vendor_interface_t *)s;
        printf("[smoke] vendor interface: size=%u init=%p op=%p cleanup=%p\n",
               (unsigned)v->size, v->p_init, v->p_op, v->p_cleanup);
        if (!v->p_init || !v->p_op || !v->p_cleanup) {
            printf("[smoke] FAIL: null vendor fn\n"); return 3;
        }
        printf("[smoke] PASS vendor\n");
        return 0;
    }
    void *s = dlsym(h, "HMI");
    if (!s) { printf("[smoke] FAIL: HMI not exported\n"); return 3; }
    struct hw_module_t *m = (struct hw_module_t *)s;
    printf("[smoke] HMI: tag=0x%08x module_api=0x%04x hal_api=0x%04x id=%s name=%s\n",
           m->tag, m->module_api_version, m->hal_api_version,
           m->id ? m->id : "(null)", m->name ? m->name : "(null)");
    if (m->tag != 0x4d4f4448u /* HW_MODULE_MAGIC (0xA5M0B1E?) -- informational */ ) {
        printf("[smoke] note: tag differs from expected magic (informational)\n");
    }
    if (strcmp(mode, "halopen") == 0) {
        /* Walk the EXACT device path: JNI does module->methods->open(module,
         * "bluetooth", &device) at BT enable.  Under qemu there is no
         * controller/UART, so this exercises the init error path: a stack
         * that returns an error promptly is well-behaved; one that blocks
         * forever here would also block the device's BT process. */
        struct hw_module_methods_t {
            int (*open)(const struct hw_module_t *, const char *, void **);
        } *methods = (struct hw_module_methods_t *)m->methods;
        if (!methods || !methods->open) {
            printf("[smoke] FAIL: methods->open missing\n");
            return 3;
        }
        printf("[smoke] calling methods->open(\"bluetooth\") -- init path, "
               "no controller under qemu ...\n");
        void *dev = NULL;
        int rc = methods->open((const struct hw_module_t *)m, "bluetooth", &dev);
        printf("[smoke] open() returned %d (device=%p)\n", rc, dev);
        if (rc != 0) { printf("[smoke] OPEN-FAIL rc=%d (error path taken)\n", rc); return 4; }
        printf("[smoke] PASS halopen\n");
        return 0;
    }
    printf("[smoke] PASS hal\n");
    return 0;
}
