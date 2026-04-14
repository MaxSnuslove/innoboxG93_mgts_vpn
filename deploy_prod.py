"""
PRODUCTION deployment: Phase 2 with stack=gvisor + watchdog + log rotation.
Auto-recovers on sing-box crash. Manual kill via /etc/luci-uploads/SB_STOP touch.
Disable by /etc/luci-uploads/SB_DISABLE (rc.local skips on next boot).

Files created on router:
  /tmp/singbox/sing-box            — 56MB binary (re-downloaded on boot)
  /tmp/singbox/config.json         — config
  /tmp/singbox/run.log             — rc.local trace
  /etc/luci-uploads/sb.log         — sing-box debug log (rotated at 512KB)
  /etc/luci-uploads/phase2_run.log — persistent copy of run.log

Control:
  kill_singbox.py  — stop and disable
  enable_singbox.py — re-enable
"""
import json, base64, urllib.request, os, sys

sys.stdout.reconfigure(encoding='utf-8')
os.environ.pop('http_proxy', None); os.environ.pop('HTTP_PROXY', None)

with open('C:/обход/sb_phase2.json', encoding='utf-8') as f:
    cfg = json.load(f)

# stack: system -> gvisor (THE FIX)
for ib in cfg['inbounds']:
    if ib.get('type') == 'tun':
        ib['stack'] = 'gvisor'

cfg_json = json.dumps(cfg, separators=(',', ':'))
cfg_b64 = base64.b64encode(cfg_json.encode()).decode()
print(f'config: stack=gvisor, bytes={len(cfg_json)}')

SB_URL = ('https://github.com/SagerNet/sing-box/releases/download/'
          'v1.14.0-alpha.12/sing-box-1.14.0-alpha.12-linux-armv5.tar.gz')

rclocal = r'''# MGTS mesh init
echo "[mesh] status set to init"
uci set wireless.mesh.status=init

# === sing-box PROD (stack=gvisor + watchdog) ===

# Emergency disable — touch /etc/luci-uploads/SB_DISABLE to skip sing-box on boot
if [ -f /etc/luci-uploads/SB_DISABLE ]; then
  echo "[sb] SKIPPED by SB_DISABLE flag at $(date)" >> /etc/luci-uploads/phase2_run.log
  exit 0
fi

LOG=/tmp/singbox/run.log
PLOG=/etc/luci-uploads/phase2_run.log
SBLOG=/etc/luci-uploads/sb.log
mkdir -p /tmp/singbox
echo "[sb] boot $(date)" > $LOG

# Decode config
printf '%s' "CFG_B64_PLACEHOLDER" | base64 -d > /tmp/singbox/config.json

# Wait for WAN+DNS
sleep 20
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  nslookup github.com >/dev/null 2>&1 && { echo "[sb] DNS ok ($i)" >> $LOG; break; }
  sleep 10
done

# Download sing-box if not present
if [ ! -x /tmp/singbox/sing-box ]; then
  echo "[sb] download start $(date)" >> $LOG
  for a in 1 2 3 4 5 6 7 8; do
    /usr/bin/wget --no-check-certificate --tries=1 --timeout=90 \
      -O /tmp/singbox/sb.tgz 'SB_URL_PLACEHOLDER' 2>> /tmp/singbox/dl.log
    SZ=$(wc -c < /tmp/singbox/sb.tgz 2>/dev/null)
    echo "[sb] attempt $a: $SZ bytes" >> $LOG
    [ "$SZ" -gt 10000000 ] && break
    sleep 10
  done
  cd /tmp/singbox
  tar -xzf sb.tgz 2>> /tmp/singbox/dl.log
  for f in /tmp/singbox/*/sing-box; do [ -f "$f" ] && mv "$f" /tmp/singbox/sing-box; done
  chmod +x /tmp/singbox/sing-box
  rm -rf /tmp/singbox/sb.tgz /tmp/singbox/sing-box-*-linux-armv*
fi
[ ! -x /tmp/singbox/sing-box ] && {
  echo "[sb] FATAL: no binary, giving up" >> $LOG
  cp -f $LOG $PLOG
  exit 0
}

# Validate config before starting
if ! /tmp/singbox/sing-box check -c /tmp/singbox/config.json >> $LOG 2>&1; then
  echo "[sb] FATAL: config invalid" >> $LOG
  cp -f $LOG $PLOG
  exit 0
fi

# Relax RPF for tun0 so return traffic from sing-box isn't filtered
echo 2 > /proc/sys/net/ipv4/conf/all/rp_filter

# Kill any leftover sing-box from previous run (shouldn't be any on boot)
PIDS=$(pgrep -f "sing-box run"); for P in $PIDS; do kill -9 $P 2>/dev/null; done
sleep 1

# First launch
echo "[sb] launch $(date)" >> $LOG
(/tmp/singbox/sing-box run -c /tmp/singbox/config.json >> $SBLOG 2>&1) &
sleep 15

# Wait for tun0 to come up (up to 40s)
for i in 1 2 3 4 5 6 7 8; do
  if /sbin/ip link show tun0 >/dev/null 2>&1; then
    echo "[sb] tun0 up after $((i*5))s" >> $LOG
    break
  fi
  sleep 5
done

# Set loose rp_filter on tun0 (needs tun0 to exist)
echo 0 > /proc/sys/net/ipv4/conf/tun0/rp_filter 2>/dev/null

# Open firewall: allow LAN<->tun0 forwarding and INPUT from tun0
/usr/sbin/iptables -I FORWARD -i br-lan -o tun0 -j ACCEPT 2>/dev/null
/usr/sbin/iptables -I FORWARD -i tun0 -o br-lan -j ACCEPT 2>/dev/null
/usr/sbin/iptables -I INPUT -i tun0 -j ACCEPT 2>/dev/null

echo "[sb] iptables/sysctl applied" >> $LOG
cp -f $LOG $PLOG

# === Watchdog (runs in background forever) ===
(
  FAIL=0
  HEALTHY_SINCE=$(date +%s)
  while true; do
    sleep 10

    # Manual stop request
    if [ -f /etc/luci-uploads/SB_STOP ]; then
      echo "[wd] STOP requested at $(date)" >> $LOG
      PIDS=$(pgrep -f "sing-box run"); for P in $PIDS; do kill -TERM $P 2>/dev/null; done
      sleep 3
      PIDS=$(pgrep -f "sing-box run"); for P in $PIDS; do kill -9 $P 2>/dev/null; done
      /usr/sbin/iptables -D FORWARD -i br-lan -o tun0 -j ACCEPT 2>/dev/null
      /usr/sbin/iptables -D FORWARD -i tun0 -o br-lan -j ACCEPT 2>/dev/null
      /usr/sbin/iptables -D INPUT -i tun0 -j ACCEPT 2>/dev/null
      rm -f /etc/luci-uploads/SB_STOP
      cp -f $LOG $PLOG
      exit 0
    fi

    # Process alive check
    if pgrep -f "sing-box run" >/dev/null 2>&1; then
      NOW=$(date +%s)
      # Reset FAIL if running stable for 60s
      [ $((NOW - HEALTHY_SINCE)) -gt 60 ] && FAIL=0
      # Log rotation
      SIZE=$(wc -c < $SBLOG 2>/dev/null)
      if [ "${SIZE:-0}" -gt 524288 ]; then
        tail -100 $SBLOG > ${SBLOG}.new 2>/dev/null
        mv ${SBLOG}.new $SBLOG 2>/dev/null
        echo "[wd] rotated sb.log at $(date)" >> $LOG
      fi
      continue
    fi

    # Dead - restart
    FAIL=$((FAIL + 1))
    HEALTHY_SINCE=$(date +%s)
    echo "[wd] sing-box DOWN, restart #$FAIL at $(date)" >> $LOG

    if [ "$FAIL" -gt 20 ]; then
      echo "[wd] too many restarts, backing off 5 min" >> $LOG
      cp -f $LOG $PLOG
      sleep 300
      FAIL=0
      HEALTHY_SINCE=$(date +%s)
    fi

    (/tmp/singbox/sing-box run -c /tmp/singbox/config.json >> $SBLOG 2>&1) &
    sleep 8

    # Re-apply iptables in case flushed
    /usr/sbin/iptables -C FORWARD -i br-lan -o tun0 -j ACCEPT 2>/dev/null || \
      /usr/sbin/iptables -I FORWARD -i br-lan -o tun0 -j ACCEPT 2>/dev/null
    /usr/sbin/iptables -C FORWARD -i tun0 -o br-lan -j ACCEPT 2>/dev/null || \
      /usr/sbin/iptables -I FORWARD -i tun0 -o br-lan -j ACCEPT 2>/dev/null
    /usr/sbin/iptables -C INPUT -i tun0 -j ACCEPT 2>/dev/null || \
      /usr/sbin/iptables -I INPUT -i tun0 -j ACCEPT 2>/dev/null

    echo 0 > /proc/sys/net/ipv4/conf/tun0/rp_filter 2>/dev/null
    cp -f $LOG $PLOG
  done
) &

echo "[sb] watchdog pid=$! $(date)" >> $LOG
cp -f $LOG $PLOG
exit 0
'''

rclocal = rclocal.replace('CFG_B64_PLACEHOLDER', cfg_b64)
rclocal = rclocal.replace('SB_URL_PLACEHOLDER', SB_URL)
print(f'rc.local size: {len(rclocal)}')

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def ubus(p):
    r = urllib.request.Request('http://192.168.1.1/ubus/',
        data=json.dumps(p).encode(), headers={'Content-Type':'application/json'})
    with opener.open(r, timeout=15) as x: return json.loads(x.read())

d = ubus({'jsonrpc':'2.0','id':0,'method':'call',
         'params':['00000000000000000000000000000000','session','login',
                   {'username':'admin','password':'admin'}]})
tok = d['result'][1]['ubus_rpc_session']
print('auth ok')

# Clear any stale flag files
for p in ['/etc/luci-uploads/SB_STOP','/etc/luci-uploads/SB_DISABLE',
          '/etc/luci-uploads/phase2_run.log','/etc/luci-uploads/sb.log',
          '/etc/luci-uploads/diag.txt']:
    ubus({'jsonrpc':'2.0','id':1,'method':'call','params':[tok,'file','remove',{'path':p}]})

r = ubus({'jsonrpc':'2.0','id':1,'method':'call',
        'params':[tok,'file','write',{'path':'/etc/rc.local','data':rclocal}]})
print(f'write: {r.get("result")}')

r = ubus({'jsonrpc':'2.0','id':2,'method':'call',
        'params':[tok,'system','reboot',{}]})
print(f'reboot: {r.get("result")}')
print('Wait ~3-4 min for boot + download + startup.')
