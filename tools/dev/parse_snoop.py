#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_snoop.py — 解析 Fluoride(r36) 写出的 btsnoop 文件，验证我们下发的 HCI 参数

为什么要自己解析
----------------
第二台蓝牙设备不总是在手边，而"COD 是不是 0x240414""是不是真的进了可发现模式"
这类问题在 **HCI 层**有确定答案。btsnoop 落盘（scripts/device/enable_snoop.sh 打开）
后，本地解析即可拿到 Write Class of Device / Write Scan Enable 的实际字节。

文件格式（依据 src/hci/src/btsnoop.c）
--------------------------------------
16 字节文件头： "btsnoop\\0" + version(4,BE) + datalink(4,BE=0x3ea=1002)
每条记录 24 字节头（全部 BE）：
    original_length, included_length, packet_flags, cumulative_drops,
    timestamp_hi, timestamp_lo            （时间戳单位 µs，基准是公元 0 年）
随后 included_length 字节数据：**第 1 字节是 H4 包类型**（1=Command,2=ACL,3=SCO,4=Event），
其余是 HCI 包本体。packet_flags：Command=2, Event=3, ACL/SCO=is_received(0/1)。

用法
----
  python3 tools/dev/parse_snoop.py <btsnoop_hci.log> [--hci] [--avdtp] [--sdp] [--l2cap]
"""
import argparse
import struct
import sys

# ---- HCI 命令 opcode（我们关心的）----
HCI_CMDS = {
    0x0C1A: ("Write_Scan_Enable", 1),
    0x0C24: ("Write_Class_of_Device", 3),
    0x0C13: ("Write_Local_Name", 248),
    0x0C03: ("Reset", 0),
    0x0C01: ("Read_Local_Version_Information", 0),
    0x041E: ("LE_Set_Event_Mask?", 0),
    0x0C36: ("Read_Inquiry_Scan_Activity", 0),
    0x0C1E: ("Read_Scan_Enable", 0),
    0x0C23: ("Read_Class_of_Device", 0),
    0x0406: ("Write_Authentication_Enable", 1),
    0x0C37: ("Read_Page_Scan_Activity", 0),
    0x0416: ("Write_Extended_Inquiry_Response?", 0),
}

SCAN_ENABLE = {0x00: "NON_DISCOVERABLE + NON_CONNECTABLE",
               0x01: "inquiry scan only (discoverable, not connectable)",
               0x02: "page scan only (connectable, NOT discoverable)",
               0x03: "inquiry + page scan (discoverable AND connectable)"}


def major_minor(cod: int) -> str:
    """COD(24bit) → 语义分解（Bluetooth Assigned Numbers）"""
    minor = (cod >> 2) & 0x3F
    major = (cod >> 8) & 0x1F
    svc = (cod >> 13) & 0x7FF
    major_names = {0x01: "Computer", 0x02: "Phone", 0x03: "LAN/Network AP",
                   0x04: "Audio/Video", 0x05: "Peripheral", 0x06: "Imaging"}
    av_minor = {0x01: "Wearable Headset", 0x02: "Hands-free", 0x04: "Microphone",
                0x05: "Loudspeaker", 0x06: "Headphones", 0x07: "Portable Audio",
                0x0A: "HiFi Audio", 0x0D: "Gaming"}
    svc_names = [(0x01, "Limited"), (0x08, "Positioning"), (0x10, "Networking"),
                 (0x20, "Rendering"), (0x40, "Capturing"), (0x80, "Object Transfer"),
                 (0x100, "Audio"), (0x200, "Telephony"), (0x400, "Information")]
    svcs = [n for b, n in svc_names if svc & b]
    s = f"COD=0x{cod:06X}  major=0x{major:02X}({major_names.get(major,'?')})"
    if major == 0x04:
        s += f"  minor=0x{minor:02X}({av_minor.get(minor,'?')})"
    else:
        s += f"  minor=0x{minor:02X}"
    s += "  services=[" + ",".join(svcs) + "]"
    return s


def read_records(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"btsnoop\x00":
        raise SystemExit(f"不是 btsnoop 文件（magic={data[:8]!r}）")
    version, datalink = struct.unpack(">II", data[8:16])
    yield {"__header__": True, "version": version, "datalink": datalink, "size": len(data)}
    off = 16
    n = len(data)
    idx = 0
    while off + 24 <= n:
        olen, ilen, flags, drops, thi, tlo = struct.unpack(">IIIIII", data[off:off + 24])
        off += 24
        if ilen == 0 or off + ilen > n:
            break
        pkt = data[off:off + ilen]
        off += ilen
        idx += 1
        yield {"idx": idx, "olen": olen, "ilen": ilen, "flags": flags,
               "drops": drops, "ts": ((thi << 32) | tlo), "data": pkt}


def l2cap_channels_summary(recs):
    """粗略统计 L2CAP 信令：connection request 的 PSM（AVDTP=0x19/0x1B, SDP=0x01, AVCTP=0x17）"""
    psm_names = {0x01: "SDP", 0x03: "RFCOMM", 0x17: "AVCTP", 0x19: "AVDTP signaling",
                 0x1B: "AVDTP media(transport)"}
    found = []
    for r in recs:
        d = r["data"]
        if not d or d[0] != 2 or len(d) < 13:
            continue
        # H4(1) + ACL header(4) + L2CAP header(4) + CID...
        acl_len = d[3] | (d[4] << 8) if False else (d[3] << 8 | d[4])
        pb = (d[2] >> 4) & 0x3
        if pb != 0x2:            # 只看起始包（PB=10b）
            continue
        l2len = d[5] | (d[6] << 8)
        cid = d[7] | (d[8] << 8)
        if cid != 0x0001 or len(d) < 13:   # 只解析信令通道
            continue
        code = d[9]
        if code == 0x02 and len(d) >= 15:   # L2CAP Connection Request
            # payload: code(1) id(1) len(2) PSM(2) SourceCID(2)
            # 注意 PSM 在 payload 偏移 4 → d[13],d[14]；早先把 d[11..12]（length）当成 PSM，
            # 导致所有请求都被误报成 "PSM 0x0004"，SDP/AVDTP 全被藏起来（P1c 排障时踩到）
            psm = d[13] | (d[14] << 8)
            found.append((r["idx"], psm_names.get(psm, f"PSM 0x{psm:04X}"),
                          "rx" if r["flags"] & 1 else "tx",
                          f"id={d[10]} len={d[11] | (d[12] << 8)} scid=0x{d[15] | (d[16] << 8):04X}"))
    return found



# ============================================================================
# AVDTP 信令解码（P1c/P2/P3 的核心取证工具：协商出来的 codec 参数就在这里）
# ============================================================================
AVDT_SIG = {0x01: "DISCOVER", 0x02: "GET_CAPABILITIES", 0x03: "SET_CONFIGURATION",
            0x04: "GET_CONFIGURATION", 0x05: "RECONFIGURE", 0x06: "OPEN",
            0x07: "START", 0x08: "CLOSE", 0x09: "SUSPEND", 0x0A: "ABORT",
            0x0B: "SECURITY_CONTROL", 0x0C: "GET_ALL_CAPABILITIES", 0x0D: "DELAY_REPORT"}
# 头部布局来自 stack/avdt/avdt_defs.h（**不是** AVDTP 规范文字里那种顺序，别照抄文档）：
#   AVDT_MSG_PRS_HDR: label = o0>>4;  pkt_type = (o0>>2)&3;  msg_type = o0&3
#   AVDT_MSG_PRS_SIG: sig = o1 & 0x3F
#   AVDT_MSG_TYPE_CMD=0 / GRJ=1 / RSP=2 / REJ=3
AVDT_MT = {0: "CMD", 1: "GEN_REJECT", 2: "RSP", 3: "REJ"}
AVDT_ERR = {0x01: "HEADER_COMPRESSION", 0x11: "NO_RES_AVAIL", 0x12: "INVALID_CATEGORY",
            0x13: "OUT_OF_RANGE", 0x14: "INVALID_PAYLOAD", 0x17: "INVALID_CAPABILITIES",
            0x18: "INVALID_MEDIA_TYPE", 0x19: "INVALID_TRANSPORT", 0x1a: "INVALID_DIRECTION",
            0x1b: "INVALID_ACP_SEID", 0x1c: "INVALID_INT_SEID", 0x1d: "SEP_IN_USE",
            0x1e: "NOT_IN_USE", 0x1f: "INVALID_CODEC", 0x20: "INVALID_SECURITY"}
AVDT_CAT = {0x01: "MEDIA_TRANSPORT", 0x02: "REPORTING", 0x03: "RECOVERY",
            0x04: "CONTENT_PROTECTION", 0x05: "HEADER_COMPRESSION", 0x06: "MULTIPLEXING",
            0x07: "MEDIA_CODEC", 0x08: "DELAY_REPORTING"}
CODEC_NAMES = {0x00: "SBC", 0x01: "MPEG-1,2 Audio", 0x02: "AAC/MPEG-2,4", 0xFF: "VENDOR(A2DP: LDAC/aptX…)"}


def dec_sbc_info(b: bytes) -> str:
    """A2DP SBC codec-specific info element 的 4 字节能力/配置（media_type、codec_type 之后）"""
    if len(b) < 4:
        return "SBC(截断)"
    sf, cm, bl, sb_, al, mn, mx = (b[0] & 0xF0), (b[0] & 0x0F), (b[1] & 0xF0), \
        (b[1] & 0x0C), (b[1] & 0x03), b[2], b[3]
    f = lambda v, t: {0x80: "16k", 0x40: "32k", 0x20: "44.1k", 0x10: "48k"}[v] if v in t else "?"
    freqs = [n for v, n in ((0x80, "16k"), (0x40, "32k"), (0x20, "44.1k"), (0x10, "48k")) if sf & v]
    modes = [n for v, n in ((0x08, "MONO"), (0x04, "DUAL"), (0x02, "STEREO"), (0x01, "JOINT")) if cm & v]
    blocks = [n for v, n in ((0x80, "4"), (0x40, "8"), (0x20, "12"), (0x10, "16")) if bl & v]
    subs = [n for v, n in ((0x08, "4"), (0x04, "8")) if sb_ & v]
    allocs = [n for v, n in ((0x02, "SNR"), (0x01, "LOUDNESS")) if al & v]
    return (f"SBC freq[{'|'.join(freqs) or '无!'}] mode[{'|'.join(modes)}] "
            f"blocks[{'|'.join(blocks)}] subbands[{'|'.join(subs)}] alloc[{'|'.join(allocs)}] "
            f"bitpool {mn}..{mx}")


def dec_codec_info(b: bytes) -> str:
    """service category = MEDIA_CODEC 的 value：media_type(1) + codec_type(1) + codec info"""
    if len(b) < 2:
        return "codec info 截断"
    mt, ct = b[0] >> 4, b[1]
    name = CODEC_NAMES.get(ct, f"codec 0x{ct:02X}")
    if ct == 0x00:
        return f"{name}: " + dec_sbc_info(b[2:6])
    if ct == 0x02:  # AAC
        if len(b) >= 6:
            sr = (b[2] << 4) | (b[3] >> 4)
            srs = [n for v, n in ((0x80, "96k"), (0x40, "88.2k"), (0x20, "64k"), (0x10, "48k"),
                                  (0x08, "44.1k"), (0x04, "32k"), (0x02, "24k"), (0x01, "22.05k"))
                   if sr & v]
            return f"{name}: freq[{'|'.join(srs) or '无!'}] channels={b[3] & 0x0C} vbr={b[5] & 0x80 and 1 or 0}"
        return name
    if ct == 0xFF:  # vendor
        if len(b) >= 6:
            vid = b[2] | (b[3] << 8) | (b[4] << 16) | (b[5] << 24)
            vendors = {0x00000001: "LDAC(sony)", 0x0000004F: "aptX", 0x0000000A: "aptX? "}
            return f"{name}: vendor_id=0x{vid:08X} ({vendors.get(vid, '?')}) codec_id=0x{b[6] | (b[7] << 8) if len(b) >= 8 else 0:04X}"
        return name
    return f"{name}: {' '.join('%02x' % x for x in b[2:])}"


def dec_categories(b: bytes) -> list:
    """SET_CONFIGURATION / GET_CAPABILITIES 应答里的 service category 列表"""
    out, i = [], 0
    while i + 2 <= len(b):
        cat, ln = b[i], b[i + 1]
        val = b[i + 2:i + 2 + ln]
        nm = AVDT_CAT.get(cat, f"CAT 0x{cat:02X}")
        if cat == 0x07:
            out.append(f"{nm}={dec_codec_info(val)}")
        else:
            out.append(f"{nm}(len={ln}{' ' + val.hex() if val else ''})")
        i += 2 + ln
    return out


def avdtp_decode(recs):
    """按 L2CAP 信令建立 CID↔PSM 映射，然后解码 AVDTP signaling（PSM 0x19）"""
    cid2psm = {}          # 本端 CID -> psm（由对端发来的 conn req/resp 学到）
    pend = {}             # identifier -> (psm, scid)
    lines = []
    for r in recs:
        d = r["data"]
        if not d or d[0] != 2 or len(d) < 9:
            continue
        pb = (d[2] >> 4) & 0x3
        l2len = d[5] | (d[6] << 8)
        cid = d[7] | (d[8] << 8)
        pay = d[9:9 + l2len]
        rx = bool(r["flags"] & 1)
        dirn = "rx" if rx else "tx"
        if pb == 2 and cid == 0x0001 and len(pay) >= 4:
            code, ident, plen = pay[0], pay[1], pay[2] | (pay[3] << 8)
            body = pay[4:4 + plen]
            if code == 0x02 and len(body) >= 4:          # Connection Request
                psm, scid = body[0] | (body[1] << 8), body[2] | (body[3] << 8)
                pend[ident] = (psm, scid)
                lines.append((r["idx"], f"L2CAP {dirn} ConnReq id={ident} "
                                        f"PSM=0x{psm:04X} scid=0x{scid:04X}"))
            elif code == 0x03 and len(body) >= 4:         # Connection Response
                dcid, scid, result = body[0] | (body[1] << 8), body[2] | (body[3] << 8), body[4] if len(body) > 4 else 0
                if ident in pend:
                    psm, _ = pend[ident]
                    # 对端 CID=scid ↔ 我方 CID=dcid
                    cid2psm[scid] = psm
                    if dcid:
                        cid2psm[dcid] = psm
                lines.append((r["idx"], f"L2CAP {dirn} ConnRsp id={ident} dcid=0x{dcid:04X} "
                                        f"scid=0x{scid:04X} result={result}"
                                        f"{'  ← 被拒绝!' if result else ''}"))
            continue
        # 数据通道：只看 AVDTP signaling
        if pb != 2 or cid not in cid2psm or cid2psm[cid] != 0x19 or len(pay) < 2:
            continue
        o0, o1 = pay[0], pay[1]
        label, pkt_t, msg_t, sig = o0 >> 4, (o0 >> 2) & 0x3, o0 & 0x3, o1 & 0x3F
        name = AVDT_SIG.get(sig, f"SIG 0x{sig:02X}")
        mt = AVDT_MT.get(msg_t, f"MT {msg_t}")
        rest = pay[2:]
        extra = ""
        if msg_t == 0:               # command
            if sig in (0x03, 0x05) and rest:          # SET_CONFIGURATION / RECONFIGURE
                if sig == 0x03 and len(rest) >= 1:
                    extra = f" ACP_SEID={rest[0] >> 2} INT_SEID={rest[1] >> 2 if len(rest) > 1 else '?'} :: " + \
                            "; ".join(dec_categories(rest[2:]))
                else:
                    extra = f" SEID={rest[0] >> 2 if rest else '?'} :: " + "; ".join(dec_categories(rest[1:]))
            elif sig == 0x02 and rest:                # GET_CAPABILITIES
                extra = f" ACP_SEID={rest[0] >> 2}"
            elif sig == 0x01 and rest:                # DISCOVER
                extra = f" :: " + "; ".join(dec_categories(rest))
            elif sig == 0x07 and rest:                # START
                extra = " SEIDs=" + ",".join(str(x >> 2) for x in rest)
        elif msg_t == 2:             # response accept
            if sig == 0x02 and rest:                  # GET_CAPABILITIES rsp = service category 列表
                extra = " :: " + "; ".join(dec_categories(rest))
            elif sig == 0x01 and rest:                # DISCOVER rsp = SEP 列表（2 字节一条）
                seps = []
                for i in range(0, len(rest) - 1, 2):
                    b0, b1 = rest[i], rest[i + 1]
                    seps.append(f"SEID={b0 >> 2} inUse={(b0 >> 1) & 1} "
                                f"media={'audio' if (b1 >> 4) == 0 else b1 >> 4} "
                                f"tsep={'SNK' if (b1 >> 3) & 1 else 'SRC'}")
                extra = " :: " + " | ".join(seps)
        elif msg_t == 3:             # reject
            if len(rest) >= 2:
                extra = (f" category={AVDT_CAT.get(rest[0], hex(rest[0]))} "
                         f"error={AVDT_ERR.get(rest[1], '0x%02X' % rest[1])}")
            elif rest:
                extra = f" service_category={AVDT_CAT.get(rest[0], hex(rest[0]))}"
        lines.append((r["idx"], f"AVDTP {dirn} {name} {mt} label={label}{extra}"
                                f"   raw={pay[:24].hex()}"))
    return lines


# ============================================================================
# HCI 事件解码（连接层排障：谁连上来、什么 COD、为什么断开）
# ============================================================================
HCI_EV = {0x03: "Connection_Complete", 0x04: "Connection_Request", 0x05: "Disconnection_Complete",
          0x06: "Auth_Complete", 0x07: "Remote_Name_Request_Complete", 0x08: "Encryption_Change",
          0x0B: "Read_Remote_Version_Complete", 0x0C: "Command_Complete", 0x0D: "Command_Status",
          0x12: "Role_Change", 0x13: "Number_Of_Completed_Packets", 0x1B: "Max_Slots_Change",
          0x23: "QoS_Violation", 0x31: "IO_Capability_Request", 0x32: "IO_Capability_Response",
          0x33: "User_Confirmation_Request", 0x36: "Simple_Pairing_Complete",
          0x38: "Link_Supervision_Timeout_Changed", 0x3E: "LE_Meta", 0xFF: "Vendor_Diagnostic"}
HCI_DISC = {0x05: "AUTH_FAILURE", 0x08: "CONN_TIMEOUT(supervision)", 0x13: "REMOTE_USER_TERMINATED",
            0x14: "REMOTE_LOW_RESOURCES", 0x15: "REMOTE_POWER_OFF", 0x16: "LOCAL_HOST_TERMINATED",
            0x1A: "UNSUPPORTED_REMOTE_FEATURE", 0x22: "LMP_RESPONSE_TIMEOUT",
            0x28: "INSTANT_PASSED", 0x29: "PAIRING_WITH_UNIT_KEY_NOT_SUPPORTED",
            0x3B: "UNACCEPTABLE_CONN_PARAMETERS", 0x3D: "CONN_TERMINATED_MIC_FAILURE"}


def bd(b: bytes) -> str:
    return ":".join("%02X" % x for x in reversed(b[:6]))


def hci_events_decode(recs, interesting=None):
    out = []
    for r in recs:
        d = r["data"]
        if not d or d[0] != 4 or len(d) < 3:
            continue
        code, plen = d[1], d[2]
        if code == 0x13:                      # Number_Of_Completed_Packets 太吵，默认跳过
            continue
        if interesting and code not in interesting:
            continue
        b = d[3:3 + plen]
        nm = HCI_EV.get(code, f"EVENT 0x{code:02X}")
        det = ""
        if code == 0x04 and len(b) >= 9:      # Connection_Request
            det = f"bdaddr={bd(b)} COD=0x{b[6] | (b[7] << 8) | (b[8] << 16):06X} link={'SCO' if b[9] else 'ACL'}"
        elif code == 0x03 and len(b) >= 11:   # Connection_Complete
            det = (f"status=0x{b[0]:02X} handle=0x{b[1] | (b[2] << 8):04X} bdaddr={bd(b[3:9])} "
                   f"link={ {0: 'ACL', 1: 'SCO', 2: 'eSCO'}.get(b[9], b[9])} "
                   f"enc={'on' if b[10] else 'off'}")
        elif code == 0x05 and len(b) >= 4:    # Disconnection_Complete
            det = (f"status=0x{b[0]:02X} handle=0x{b[1] | (b[2] << 8):04X} "
                   f"reason=0x{b[3]:02X}({HCI_DISC.get(b[3], '?')})")
        elif code == 0x07 and len(b) >= 7:    # Remote_Name_Request_Complete
            nm_ = b[7:].split(b"\x00")[0].decode("utf8", "replace")
            det = f"status=0x{b[0]:02X} bdaddr={bd(b[1:7])} name={nm_!r}"
        elif code == 0x08 and len(b) >= 4:
            det = f"status=0x{b[0]:02X} handle=0x{b[1] | (b[2] << 8):04X} enc={'ON' if b[3] else 'OFF'}"
        elif code == 0x06 and len(b) >= 4:
            det = f"status=0x{b[0]:02X} handle=0x{b[1] | (b[2] << 8):04X}"
        elif code == 0x33 and len(b) >= 7:
            det = f"bdaddr={bd(b)} numeric=0x{int.from_bytes(b[6:9], 'little'):06X}" if len(b) >= 9 else f"bdaddr={bd(b)}"
        elif code == 0x3E and len(b) >= 2:
            det = f"subevent=0x{b[0]:02X}"
        out.append((r["idx"], f"{nm}: {det}" if det else nm))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snoop")
    ap.add_argument("--hci", action="store_true", help="打印关心的 HCI 命令/事件")
    ap.add_argument("--cod", action="store_true", help="只看 Class of Device / Scan Enable")
    ap.add_argument("--l2cap", action="store_true", help="列出 L2CAP 连接请求的 PSM（已修 off-by-two）")
    ap.add_argument("--avdtp", action="store_true", help="解码 AVDTP 信令：GET_CAPS/SET_CONFIG/START 及其 codec 参数")
    ap.add_argument("--events", action="store_true", help="解码 HCI 事件（连接/断开原因/远端名/COD/加密）")
    ap.add_argument("--all-cmds", action="store_true", help="打印所有 HCI 命令 opcode")
    ap.add_argument("--max", type=int, default=400)
    args = ap.parse_args()

    recs = list(read_records(args.snoop))
    if not recs:
        print("空文件")
        return
    hdr = recs[0]
    body = recs[1:]
    print(f"文件: {args.snoop}")
    print(f"  btsnoop version={hdr['version']} datalink={hdr['datalink']} "
          f"(1002 = H4 with pseudo-header) size={hdr['size']} records={len(body)}")

    cmd_hist = {}
    cod_hits, scan_hits = [], []
    for r in body:
        d = r["data"]
        if not d:
            continue
        ptype = d[0]
        pkt = d[1:]
        if ptype == 1 and len(pkt) >= 3:      # HCI Command
            op = pkt[0] | (pkt[1] << 8)
            plen = pkt[2]
            params = pkt[3:3 + plen]
            name = HCI_CMDS.get(op, (f"opcode_0x{op:04X}", None))[0]
            cmd_hist[op] = cmd_hist.get(op, 0) + 1
            if op == 0x0C24 and len(params) >= 3:
                cod = params[0] | (params[1] << 8) | (params[2] << 16)
                cod_hits.append((r["idx"], cod, params[:3].hex(" ")))
            if op == 0x0C1A and params:
                scan_hits.append((r["idx"], params[0]))
            if args.all_cmds:
                print(f"  [{r['idx']:5d}] CMD {name} (0x{op:04X}) params={params[:16].hex(' ')}")

    if args.hci or args.cod or cod_hits or scan_hits:
        print("\n=== Class of Device（HCI Write_Class_of_Device, 0x0C24）===")
        if not cod_hits:
            print("  （本次抓包里没有出现该命令；说明 BT 未重启或 snoop 未生效）")
        for idx, cod, raw in cod_hits:
            print(f"  [{idx:5d}] bytes(LE)={raw}  →  {major_minor(cod)}")
        print("\n=== Scan Enable（HCI Write_Scan_Enable, 0x0C1A）===")
        if not scan_hits:
            print("  （无）")
        for idx, v in scan_hits:
            print(f"  [{idx:5d}] 0x{v:02X} = {SCAN_ENABLE.get(v, '?')}")

    if args.l2cap:
        print("\n=== L2CAP Connection Request（PSM）===")
        for idx, name, d, extra in l2cap_channels_summary(body):
            print(f"  [{idx:5d}] {d}  {name}  {extra}")

    if args.events:
        print("\n=== HCI 事件（连接层）===")
        evs = hci_events_decode(body)
        if not evs:
            print("  （无）")
        for idx, line in evs[-args.max:]:
            print(f"  [{idx:5d}] {line}")

    if args.avdtp:
        print("\n=== L2CAP / AVDTP 信令（协商全过程）===")
        lines = avdtp_decode(body)
        if not lines:
            print("  （没有 AVDTP 信令：对端根本没走到流建立，问题在 SDP/配对层）")
        for idx, line in lines[-args.max:]:
            print(f"  [{idx:5d}] {line}")

    print("\n=== HCI 命令直方图（前 20）===")
    for op, c in sorted(cmd_hist.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  0x{op:04X} {HCI_CMDS.get(op, ('?',))[0]:38s} x{c}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
