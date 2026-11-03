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
        if code == 0x02 and len(d) >= 13:   # L2CAP Connection Request
            psm = d[11] | (d[12] << 8)
            found.append((r["idx"], psm_names.get(psm, f"PSM 0x{psm:04X}"),
                          "rx" if r["flags"] & 1 else "tx"))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snoop")
    ap.add_argument("--hci", action="store_true", help="打印关心的 HCI 命令/事件")
    ap.add_argument("--cod", action="store_true", help="只看 Class of Device / Scan Enable")
    ap.add_argument("--l2cap", action="store_true", help="粗略列出 L2CAP 连接请求的 PSM")
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
        for idx, name, d in l2cap_channels_summary(body):
            print(f"  [{idx:5d}] {d}  {name}")

    print("\n=== HCI 命令直方图（前 20）===")
    for op, c in sorted(cmd_hist.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  0x{op:04X} {HCI_CMDS.get(op, ('?',))[0]:38s} x{c}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
