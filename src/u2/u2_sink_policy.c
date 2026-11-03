/*
 * u2_sink_policy.c — U2 蓝牙 A2DP SINK 接收器：native 策略层实现
 *
 * 本文件是**本项目自有代码**，不属于 AOSP；参考 android12 / bluez-alsa / PipeWire 的
 * 架构思路重写，未做任何文件级复制。
 *
 * 职责（P1a）：
 *   1. 用我们自己的 btav_callbacks_t 注册 A2DP **SINK**（抢在 Java init_src 之前占住唯一 AV 槽位）；
 *   2. 把 BTA_A2DP_SINK_SERVICE_ID 置入 btif_enabled_services，让 DM 的 enable 循环
 *      恰好一次地执行 btif_av_sink_execute_service(TRUE)；
 *   3. 打开 USE_AUDIO_TRACK 路径下新增的"音频焦点门禁"：native 侧自行授予 GRANTED；
 *   4. 记录协商到的采样率/声道数，并对 != 48000 打 WARNING（全局 48k 硬约束的可观测性）。
 *
 * 刻意**不做**的事（单变量纪律，留到后续循环）：
 *   - 不改 COD / 不改可发现性（P1b）
 *   - 不改 SBC sink caps 的 48k 收敛（P1c）
 *   - 不动 AVRCP 控制路径（P1d）
 */

#include "u2/u2_sink_policy.h"

#include <stdio.h>
#include <string.h>

#include <hardware/bluetooth.h>   /* bt_bdaddr_t / bt_status_t */
#include <hardware/bt_av.h>       /* btav_callbacks_t / btav_interface_t / 状态枚举 */

#include "bta_api.h"              /* BTA_A2DP_SINK_SERVICE_ID, tBTA_SERVICE_ID */
#include "btif_av.h"              /* btif_av_is_sink_enabled() */
#include "btif_common.h"          /* btif_enable_service(), btif_is_enabled() */
#include "btif_media.h"           /* btif_a2dp_set_audio_focus_state(), 焦点枚举 */
#include "osi/include/log.h"

#define U2_TAG "u2_sink_policy"

/* 全局硬约束：48kHz 全链路。此处只观测与告警，不在本循环改协商行为。 */
#define U2_TARGET_SAMPLE_RATE 48000u

/* bluetooth.c 也是这样拿接口的（AOSP r36 没有为它提供头文件声明）。
 * 定义在 btif/src/btif_av.c：const btav_interface_t *btif_av_get_sink_interface(void) */
extern const btav_interface_t *btif_av_get_sink_interface(void);

/* ---------------------------------------------------------------------------
 * 状态快照
 *
 * 静态、非热路径、无堆分配 —— 因此不涉及"新结构体单独分配 + canary"那条规矩
 * （该规矩针对 media 热路径上的动态分配）。写入都在 btif task 上，读取仅用于日志。
 * ------------------------------------------------------------------------- */
typedef struct {
    int           installed;      /* 本进程内是否已安装（跨 BT on/off 保持） */
    unsigned int  sample_rate;    /* 最近一次 audio_config_cb 的采样率 */
    unsigned int  channel_count;  /* 最近一次 audio_config_cb 的声道数 */
    int           conn_state;     /* btav_connection_state_t */
    int           audio_state;    /* btav_audio_state_t */
} u2_sink_state_t;

static u2_sink_state_t u2_state;

static void u2_fmt_bdaddr(char *out, size_t outlen, const bt_bdaddr_t *bda)
{
    if (out == NULL || outlen == 0)
        return;
    out[0] = '\0';
    if (bda == NULL) {
        snprintf(out, outlen, "(null)");
        return;
    }
    snprintf(out, outlen, "%02x:%02x:%02x:%02x:%02x:%02x",
             bda->address[0], bda->address[1], bda->address[2],
             bda->address[3], bda->address[4], bda->address[5]);
}

/* ---------------------------------------------------------------------------
 * A2DP SINK 回调（我们自己的，替代 Java A2dpSinkService 的角色）
 *
 * 注意：r36 里 bt_av_sink_callbacks 的所有使用点都有 != NULL 保护
 *       （btif_av.c:234/262/284/344/387/562/1358/1440），所以传自定义结构是安全的，
 *       而且我们能借此拿到连接/音频/配置三类事件做策略与可观测性。
 * ------------------------------------------------------------------------- */

static void u2_sink_connection_state_cb(btav_connection_state_t state, bt_bdaddr_t *bd_addr)
{
    char bda[24];
    u2_fmt_bdaddr(bda, sizeof(bda), bd_addr);
    u2_state.conn_state = (int)state;
    LOG_INFO(U2_TAG, "A2DP SINK connection_state=%d peer=%s", (int)state, bda);
}

static void u2_sink_audio_state_cb(btav_audio_state_t state, bt_bdaddr_t *bd_addr)
{
    char bda[24];
    u2_fmt_bdaddr(bda, sizeof(bda), bd_addr);
    u2_state.audio_state = (int)state;
    LOG_INFO(U2_TAG, "A2DP SINK audio_state=%d peer=%s", (int)state, bda);

#ifdef USE_AUDIO_TRACK
    if (state == BTAV_AUDIO_STATE_STARTED) {
        /* r36 的 sink 解码定时器有焦点门禁：
         *   btif_media_task.c: if (rx_audio_focus_state == BTIF_MEDIA_FOCUS_NOT_GRANTED) return;
         * 官方路径由 Java A2dpSinkService 通过 bt_av_sink_interface.set_audio_focus_state 授予；
         * 本机没有该服务 → 我们自己在 native 授予。
         * 专用接收器上不存在音频焦点竞争，"永远已授权"是正确且最简单的策略。 */
        btif_a2dp_set_audio_focus_state(BTIF_MEDIA_FOCUS_GRANTED);
        LOG_INFO(U2_TAG, "audio focus GRANTED by native policy (on STARTED)");
    }
#endif
}

static void u2_sink_audio_config_cb(bt_bdaddr_t *bd_addr, uint32_t sample_rate,
                                    uint8_t channel_count)
{
    char bda[24];
    u2_fmt_bdaddr(bda, sizeof(bda), bd_addr);
    u2_state.sample_rate   = (unsigned int)sample_rate;
    u2_state.channel_count = (unsigned int)channel_count;

    LOG_INFO(U2_TAG, "A2DP SINK audio_config peer=%s sample_rate=%u channel_count=%u",
             bda, (unsigned int)sample_rate, (unsigned int)channel_count);

    if (sample_rate != U2_TARGET_SAMPLE_RATE) {
        /* 全局硬约束要求 48kHz 全链路；44.1k 只允许作为回退。
         * P1c 会把 bta_av_co_sbc_sink_caps 收敛到 48k-only，届时本告警应消失。 */
        LOG_WARN(U2_TAG, "sample_rate=%u != %u —— 未达 48k 全链路目标（source 选了回退值）",
                 (unsigned int)sample_rate, U2_TARGET_SAMPLE_RATE);
    }
}

static btav_callbacks_t u2_sink_cbs = {
    sizeof(btav_callbacks_t),
    u2_sink_connection_state_cb,
    u2_sink_audio_state_cb,
    u2_sink_audio_config_cb,
};

/* ---------------------------------------------------------------------------
 * 安装
 * ------------------------------------------------------------------------- */

void u2_sink_policy_install(void)
{
    const btav_interface_t *sink_if = btif_av_get_sink_interface();

    LOG_INFO(U2_TAG, "install: enter (installed=%d, stack_running=%d)",
             u2_state.installed, btif_is_enabled());

    if (sink_if == NULL || sink_if->init == NULL) {
        LOG_ERROR(U2_TAG, "install: sink interface unavailable —— 放弃（不影响其它 profile）");
        return;
    }

    /* (1) 注册回调 + 建立 AV 状态机 / media task。
     *     init_sink() → btif_av_init(BTA_A2DP_SINK_SERVICE_ID)：
     *       - btif_a2dp_start_media_task()（创建 cmd 队列 + worker 线程）
     *       - btif_sm_init(btif_av_state_handlers)
     *       - btif_a2dp_on_init()（把 rx_audio_focus_state 复位为 NOT_GRANTED）
     *       - btif_enable_service(SINK)（仅在首次、即 sm_handle==NULL 时）
     *     谁先调用 btif_av_init 谁拿到 service enable 的机会，所以必须早于 Java 的 init_src。 */
    if (!btif_av_is_sink_enabled()) {
        bt_status_t st = sink_if->init(&u2_sink_cbs);
        LOG_INFO(U2_TAG, "init_sink() -> %d", (int)st);
        if (st != BT_STATUS_SUCCESS) {
            LOG_ERROR(U2_TAG, "init_sink 失败，sink 未启用");
            return;
        }
    } else {
        LOG_INFO(U2_TAG, "sink 回调已就位（BT 重启周期），跳过 init");
    }

    /* (2) 置 service 位。
     *     此刻 stack_is_running == false（btif_init_ok 在 BTA_EnableBluetooth 之前），
     *     所以只置位、不会立即执行；真正的 BTA_AvEnable 由
     *     btif_dm.c 的 BTA_DM_ENABLE_EVT service 循环调用**恰好一次**：
     *       btif_in_execute_service_request(BTA_A2DP_SINK_SERVICE_ID, TRUE)
     *         → btif_av_sink_execute_service(TRUE)
     *         → BTA_AvEnable(BTA_SEC_AUTHENTICATE,
     *                        NO_SCO_SSPD|RCCT|METADATA|VENDOR|ADV_CTRL|RCTG, bte_av_callback)
     *         → BTA_AvRegister(BTA_AV_CHNL_AUDIO, BTIF_AVK_SERVICE_NAME, 0,
     *                          bte_av_media_callback, UUID_SERVCLASS_AUDIO_SINK)
     *
     *     ★绝不在这里直接调 btif_av_sink_execute_service()：那会与上面的循环形成
     *       第二次 BTA_AvEnable，而 bta_av_api_enable() 第一件事就是
     *       memset(&bta_av_cb, 0, sizeof(tBTA_AV_CB)) —— 已注册的 AV 控制块会被整个抹掉。 */
    if (btif_is_enabled()) {
        LOG_ERROR(U2_TAG, "install 调用得太晚（stack 已在运行）：拒绝补置 service 位，"
                          "以免出现第二次 BTA_AvEnable");
    } else {
        btif_enable_service(BTA_A2DP_SINK_SERVICE_ID);
        LOG_INFO(U2_TAG, "A2DP SINK service 已置位 (service_id=%d)，等待 DM enable 循环执行",
                 (int)BTA_A2DP_SINK_SERVICE_ID);
    }

#ifdef USE_AUDIO_TRACK
    /* (3) 预授予焦点：media task 刚建立时 rx_audio_focus_state = NOT_GRANTED，
     *     提前打开门禁，避免起播瞬间丢帧。cmd 队列此时已存在（步骤 1 里同步创建），
     *     且 btif_a2dp_on_init() 的复位已经发生，不会把我们这次授予覆盖掉。 */
    btif_a2dp_set_audio_focus_state(BTIF_MEDIA_FOCUS_GRANTED);
    LOG_INFO(U2_TAG, "audio focus 预授予 GRANTED（native AudioTrack 通路）");
#else
    LOG_WARN(U2_TAG, "USE_AUDIO_TRACK 未定义：sink PCM 会走 UIPC 死路（手机上无消费者）。"
                     "请在构建里加 -DUSE_AUDIO_TRACK");
#endif

    u2_state.installed = 1;
}

/* ---------------------------------------------------------------------------
 * 可见性策略：专用接收器常驻"可发现 + 可连接"
 *
 * 为什么不能只靠 Java：Android 只在蓝牙设置界面处于前台时才把适配器设为
 * discoverable，息屏/离开界面后会降回 CONNECTABLE（甚至 NONE）。音箱必须随时
 * 能被扫到并配对，所以我们在适配器 ON 时主动设一次，并在 Java 之后每次请求
 * 降级时把它顶回去（u2_sink_policy_force_scan_mode）。
 * ------------------------------------------------------------------------- */

void u2_sink_policy_on_adapter_on(void)
{
#if (U2_ALWAYS_DISCOVERABLE == 1)
    LOG_INFO(U2_TAG, "adapter ON：设为 GENERAL_DISCOVERABLE + CONNECTABLE（接收器常驻可发现）");
    BTA_DmSetVisibility(BTA_DM_GENERAL_DISC, BTA_DM_CONN, BTA_DM_IGNORE, BTA_DM_IGNORE);
#else
    LOG_INFO(U2_TAG, "adapter ON：U2_ALWAYS_DISCOVERABLE=0，可见性交回 Java（原厂行为）");
#endif
}

void u2_sink_policy_force_scan_mode(tBTA_DM_DISC *disc_mode, tBTA_DM_CONN *conn_mode)
{
#if (U2_ALWAYS_DISCOVERABLE == 1)
    if (disc_mode == NULL || conn_mode == NULL)
        return;
    if (*disc_mode != BTA_DM_GENERAL_DISC || *conn_mode != BTA_DM_CONN) {
        LOG_WARN(U2_TAG, "覆盖上层 scan mode 请求 disc=0x%x conn=0x%x → GENERAL_DISC + CONN",
                 (unsigned int)*disc_mode, (unsigned int)*conn_mode);
        *disc_mode = BTA_DM_GENERAL_DISC;
        *conn_mode = BTA_DM_CONN;
    }
#else
    (void)disc_mode;
    (void)conn_mode;
#endif
}

unsigned int u2_sink_policy_sample_rate(void)   { return u2_state.sample_rate; }
unsigned int u2_sink_policy_channel_count(void) { return u2_state.channel_count; }
int          u2_sink_policy_is_installed(void)  { return u2_state.installed; }
