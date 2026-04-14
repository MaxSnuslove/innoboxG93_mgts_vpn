"""
Emergency rollback — restore a clean /etc/rc.local on the router and reboot.

Use if sing-box is broken or the router misbehaves. After reboot (~90s) the
router returns to its factory-ish state (just the MGTS mesh init, no sing-box,
no tun0, no custom iptables).

Prerequisites:
- You are on the LAN side of 192.168.1.1
- LuCI admin/admin password (default on Innbox G93)

Usage:
    python rollback.py
"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ubus(payload, timeout=15):
    req = urllib.request.Request(
        'http://192.168.1.1/ubus/',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read())


def login():
    d = ubus({
        'jsonrpc': '2.0', 'id': 0, 'method': 'call',
        'params': ['00000000000000000000000000000000', 'session', 'login',
                   {'username': 'admin', 'password': 'admin'}],
    })
    return d['result'][1]['ubus_rpc_session']


CLEAN_RCLOCAL = (
    '# Put your custom commands here that should be executed once\n'
    '# the system init finished. By default this file does nothing.\n'
    '\n'
    'echo "[mesh] status set to init"\n'
    'uci set wireless.mesh.status=init\n'
    '\n'
    'exit 0\n'
)


def main():
    print('>> Logging into 192.168.1.1 ...')
    try:
        tok = login()
    except Exception as e:
        print(f'!! Cannot reach router: {e}')
        print('!! Check that you are on LAN (192.168.1.0/24) and admin/admin works.')
        sys.exit(1)
    print(f'>> OK session {tok[:12]}...')

    print('>> Writing clean /etc/rc.local and empty /etc/crontabs/root ...')
    ubus([
        {'jsonrpc': '2.0', 'id': 1, 'method': 'call',
         'params': [tok, 'file', 'write',
                    {'path': '/etc/rc.local', 'data': CLEAN_RCLOCAL}]},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'call',
         'params': [tok, 'file', 'write',
                    {'path': '/etc/crontabs/root', 'data': ''}]},
    ])

    # Best-effort: kill running sing-box so the rollback takes effect immediately
    print('>> Killing live sing-box / watchdog processes ...')
    r = ubus({'jsonrpc': '2.0', 'id': 1, 'method': 'call',
              'params': [tok, 'luci', 'getProcessList', {}]})
    procs = r['result'][1].get('result', []) if r['result'][0] == 0 else []
    pids = [p['PID'] for p in procs
            if 'sing-box' in p.get('COMMAND', '')
            or 'singbox' in p.get('COMMAND', '')]
    for pid in pids:
        ubus({'jsonrpc': '2.0', 'id': 1, 'method': 'call',
              'params': [tok, 'file', 'exec',
                         {'command': '/bin/kill', 'params': ['-9', str(pid)]}]})
    print(f'>> Killed {len(pids)} processes')

    # Also drop the flag files if any
    for p in ['/etc/luci-uploads/SB_STOP', '/etc/luci-uploads/SB_DISABLE']:
        ubus({'jsonrpc': '2.0', 'id': 1, 'method': 'call',
              'params': [tok, 'file', 'remove', {'path': p}]})

    print('>> Rebooting router ...')
    try:
        ubus({'jsonrpc': '2.0', 'id': 1, 'method': 'call',
              'params': [tok, 'system', 'reboot', {}]})
    except Exception as e:
        print(f'   (reboot request dropped — normal, router is going down: {e})')

    print('\n>> Router rebooting (~90s). It will come back clean.')


if __name__ == '__main__':
    main()
