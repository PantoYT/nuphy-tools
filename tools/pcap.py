"""Read NuPhyIO <-> keyboard packets out of a USBPcap capture (.pcap, stdlib only).

    python pcap.py C:\\Windows\\Temp\\nuphy_hub2.pcap [--all]

Prints every 64-byte HID report that starts with 0x55 (host -> keyboard request) or 0xAA
(keyboard reply), in order, with direction, command number and the decoded fields
(flag, cmd, key, checksum, len, addr, handle, data).
"""
import struct
import sys

WRITE_FLAG, READ_FLAG = 0x55, 0xAA
NAMES = {47: "GetDongleName", 160: "GetBase", 161: "GetFirmwareInfo", 177: "GetDefaultKeys",
         178: "GetUseKeys", 179: "SetUseKeys", 193: "SetKeyUpload", 194: "GetMacro", 195: "SetMacro",
         209: "GetLightCount", 210: "GetKeyLightColor", 213: "GetLightState", 214: "SetLightState",
         225: "GetKeyboardFunc", 226: "SetKeyboardFunc", 238: "SetSecretKey", 239: "SetIapMode",
         241: "RestoreFactory", 243: "GetSleepInfo", 245: "SetSleepCfg", 250: "GetAppDefineSize",
         251: "GetAppDefine", 252: "SetAppDefine", 253: "GetDebugEnable", 254: "SetDebugLog"}


def records(path):
    with open(path, "rb") as f:
        head = f.read(24)
        if len(head) < 24:
            return
        endian = "<" if head[:4] == b"\xd4\xc3\xb2\xa1" else ">"
        while True:
            rh = f.read(16)
            if len(rh) < 16:
                return
            ts_s, ts_us, incl, _ = struct.unpack(endian + "IIII", rh)
            yield ts_s + ts_us / 1e6, f.read(incl)


def usb_packets(path):
    for ts, pkt in records(path):
        if len(pkt) < 27:
            continue
        hlen, irp, status, func, info, bus, dev, ep, xfer, dlen = struct.unpack_from("<HQIHBHHBBI", pkt, 0)
        data = pkt[hlen:hlen + dlen]
        yield {"ts": ts, "from_device": bool(info & 1), "bus": bus, "dev": dev,
               "ep": ep, "xfer": xfer, "data": data}


def decode(d: bytes) -> dict:
    return {"flag": d[0], "cmd": d[1], "key": d[2], "cs": d[3], "len": d[4],
            "addr": d[5] | d[6] << 8, "handle": d[7], "data": d[8:8 + max(d[4], 0)]}


def main(argv):
    path, show_all = argv[0], "--all" in argv
    t0 = None
    for p in usb_packets(path):
        d = p["data"]
        if len(d) == 65 and d[0] == 0:  # some stacks prefix report id 0
            d = d[1:]
        if len(d) != 64 or d[0] not in (WRITE_FLAG, READ_FLAG):
            continue
        t0 = t0 if t0 is not None else p["ts"]
        f = decode(d)
        if not show_all and f["cmd"] in (161, 160) and d[0] == READ_FLAG:
            pass
        arrow = "<-" if p["from_device"] else "->"
        name = NAMES.get(f["cmd"], "?")
        print(f"{p['ts'] - t0:8.3f} dev{p['dev']} ep{p['ep']:#04x} {arrow} {d[0]:02x} cmd={f['cmd']:3d} {name:16} "
              f"key={f['key']:3d} cs={f['cs']:02x} len={f['len']:3d} addr={f['addr']:5d} h={f['handle']:3d} | "
              f"{d[8:8 + min(f['len'], 56)].hex(' ')}")


if __name__ == "__main__":
    main(sys.argv[1:])
