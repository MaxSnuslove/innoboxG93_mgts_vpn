# innoboxG93_mgts_vpn

Transparent VPN (sing-box + VLESS+Reality) on the **MGTS Innbox G93** router.
All LAN clients get VPN automatically. Zero per-device configuration.

> If you are a Moscow MTS subscriber with the default Innbox G93 and you want
> every device on your Wi-Fi to bypass RKN blocks via your own VLESS sub — this
> repo does it end-to-end. Clone, fill your creds into a JSON, run one command,
> wait three minutes.

---

## Why this repo exists

Getting this to work on this exact box was painful. Most of the surprises were
not in any documentation, so here's the short list you would otherwise spend a
day finding:

1. **The CPU has no VFP / NEON.** cpuinfo reports a Cortex-A-class core but
   with the vector-float unit missing. Any armv7 hard-float Go binary crashes
   on the first floating-point op. You need the **armv5 soft-float** build of
   sing-box, not armv7. The official sing-box releases ship one:
   `sing-box-<ver>-linux-armv5.tar.gz`.

2. **`stack: "system"` in the TUN inbound silently drops TCP on this kernel.**
   You will see UDP (DNS hijack) work, `tun0` will come up, `auto_route` will
   install all the right rules — and still zero TCP traffic goes through.
   This was the multi-hour gotcha. The fix is **one line**:

   ```json
   "stack": "gvisor"
   ```

   With the gVisor userspace netstack, TCP goes through immediately. This
   repo's `sb_phase2.example.json` is already set that way.

3. **cron does not work on this firmware.** The daemon runs, `/etc/crontabs/root`
   is writable, `/etc/init.d/cron reload` returns 0, but no job ever fires
   (sandbox/seccomp bug in the MGTS build). The only reliable way to run
   anything in the background is **a bash loop launched from `/etc/rc.local`**.
   That's what `deploy_prod.py` sets up — a watchdog loop that lives as long
   as the router is up.

4. **UBUS ACL is strict.** Admin (`admin`/`admin`) is actually "poweruser"
   level, not root. `file.write` only accepts specific paths. `file.exec` is
   an allowlist (`/bin/cat`, `/bin/ubus`, `/sbin/ip ... show`, `iptables -nvxL`,
   `sysupgrade`, `reboot`, a few more). You cannot just ssh in — SSH is
   bound to the management VLAN the ISP uses, not LAN. **Everything in this
   repo talks to the router via UBUS JSON-RPC** at `http://192.168.1.1/ubus/`.

5. **Flash budget is tiny.** ~8 MB jffs2 overlay, ~15 MB usable. Single-file
   writes over ~14 MB fail. The 56 MB sing-box binary does not fit in flash,
   so it has to be re-downloaded into `/tmp` (tmpfs) on every boot. Budget
   ~2-3 minutes of boot time before the VPN is up.

---

## Architecture

```
                                  ┌───────────────┐
[LAN client 192.168.1.x] ──br-lan──▶ kernel routing
                                  │     (table 2022, all of 0/0 except
                                  │      RFC1918 + CGNAT → tun0)
                                  └──────┬────────┘
                                         │
                                         ▼
                                  /dev/net/tun  (tun0)
                                         │
                                         ▼
                               ┌──────────────────┐
                               │   sing-box       │
                               │   stack: gvisor  │
                               │   TUN inbound    │
                               │        │         │
                               │        ▼         │
                               │   VLESS+Reality  │
                               │   outbound       │
                               │   (fwmark 0x710) │
                               └────────┬─────────┘
                                        │
                                   nas0_0 (WAN)
                                        │
                                        ▼
                                     VLESS server (US / NL / UK)
                                        │
                                        ▼
                                    target internet
```

Things that are set up automatically on boot:
- `tun0` with `172.19.0.1/30`, `auto_route + strict_route`
- Table 2022 with prefix-split non-private routes via tun0
- ip rules at priorities 1808, 9000–9010 (sing-box owns these)
- fw3 FORWARD ACCEPT for `br-lan <-> tun0` and INPUT ACCEPT for `tun0`
- `net.ipv4.conf.{all,tun0}.rp_filter = {2,0}` (loose)
- DNS hijack: UDP/53 → sing-box → DoH via proxy (remote), fallback to UDP 223.5.5.5 (local)

A **watchdog** loop (still in `rc.local`, backgrounded) polls sing-box every
10 seconds. If it dies, restart. After 20 failed restarts in a row without a
60-second stable run, back off for 5 minutes. The log
(`/etc/luci-uploads/sb.log`) is truncated at 512 KB so the flash doesn't fill.

---

## Getting started

### 1. Get a VLESS+Reality subscription

You need a provider whose VLESS servers support Reality. You'll need, per
server:
- `host` and `port`
- `uuid` (usually shared across the subscription)
- `flow` (`xtls-rprx-vision` is the standard)
- `public_key` (pbk) and `short_id` (sid) for Reality
- `sni` (usually equals host)

### 2. Create your real config

```bash
cp sb_phase2.example.json sb_phase2.json
```

Edit `sb_phase2.json` and replace every `REPLACE_WITH_*` with your actual
credentials. `sb_phase2.json` is in `.gitignore` so it stays local. The
example ships with three outbounds (USA / NL / UK) and a selector defaulting
to `vless-usa` — adjust to however many servers your sub has.

### 3. Deploy

```bash
python deploy_prod.py
```

This logs into UBUS, writes the production `/etc/rc.local` (with the embedded
config and watchdog), and reboots the router. Wait 3–4 minutes for:
- boot (~30 s)
- DNS wait + sing-box download from GitHub (~1–2 min)
- sing-box startup + tun0 up (~15 s)
- VLESS handshake (~5 s)

### 4. Verify it works

```bash
python sb_control.py status
python sb_control.py log 80
python sb_control.py run
```

Open a browser on any LAN device and visit `https://api.ipify.org` — it
should return the VLESS exit IP, not your real WAN IP.

---

## Control commands

```bash
python sb_control.py status      # what's running, flag files present?
python sb_control.py log [N]     # last N lines of sing-box debug log
python sb_control.py run         # the rc.local / watchdog trace log
python sb_control.py stop        # graceful stop, one-shot — resumes next boot
python sb_control.py disable     # stop + skip on boot until re-enabled
python sb_control.py enable      # clear SB_DISABLE; takes effect next boot
```

Emergency rollback (writes a minimal safe `rc.local`, kills live sing-box,
reboots) — use if something breaks the router:

```bash
python rollback.py
```

---

## Files in this repo

| File | What it does |
|------|--------------|
| `deploy_prod.py` | Main deployer. Reads `sb_phase2.json`, writes embedded `rc.local` via UBUS, reboots. |
| `sb_control.py`  | Runtime control (stop / disable / enable / status / log). |
| `rollback.py`    | Emergency: restore clean `rc.local`, kill sing-box, reboot. |
| `sb_phase2.example.json` | Config template. Copy to `sb_phase2.json`, fill credentials. |
| `.gitignore`     | Keeps your secrets and binaries out of git. |

---

## Troubleshooting

**`sb.log` shows UDP events but zero TCP.**
You are using `stack: "system"`. Change to `"gvisor"`. This is the main gotcha
on this hardware.

**wget from the router itself times out but LAN clients work fine.**
Depends on `strict_route`. With `strict_route: true` (current default),
router-own traffic also goes through TUN. With `false`, router-own stays on
the WAN default (iif=lo is not redirected). Don't use router-side wget as the
primary health check — test from a LAN client.

**`Network is unreachable` from `dnsmasq` or `odhcpd` right after sing-box
crashes.** Stale route cache referencing the now-gone tun0. Watchdog restarts
sing-box within ~10 seconds and it self-heals. If not, `python rollback.py`.

**GitHub download fails on first attempt (881 KB partial).**
`release-assets.githubusercontent.com` TLS is flaky. The deploy retries 8×
automatically — just wait.

**`ip rule show` via UBUS returns EACCES.**
ACL only allows `route show` and `neigh show`, not `rule show`. `/etc/rc.local`
runs as root with no ACL, so the deploy collects ip rules from there into the
persistent log instead. (`python sb_control.py run` to see.)

---

## Hardware reference

| | |
|---|---|
| Model | Innbox G93 (MGTS-branded ZyXEL/Sagemcom/Innbox GPON ONT) |
| SoC | Airoha EN7523 / ECONET, ARMv7 dual-core (no VFP/NEON) |
| OS | Custom OpenWrt 2.0.0534 (LuCI + fw3 iptables legacy) |
| Kernel | 5.4.55 |
| RAM | 480 MB |
| Flash | ~8 MB overlay (jffs2) + 105 MB LxC partition (mtdblock16) |
| LAN | br-lan 192.168.1.1/24 |
| WAN | nas0_0 (GPON, PPPoE/IPoE) — CGNAT 100.110.x.x |
| Admin | http://192.168.1.1/cgi-bin/luci/  admin/admin |

---

## Credits

The gVisor-vs-system TCP discovery was made while debugging live with Claude
(Anthropic) on this exact hardware, 2026-04-15. Sharing it so the next person
with this router doesn't have to burn a day on it.

## License

MIT.
