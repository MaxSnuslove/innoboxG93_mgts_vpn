"""
Control sing-box running via watchdog on router.

Usage:
  python sb_control.py stop       — stop sing-box (watchdog reacts in ~10s)
  python sb_control.py disable    — disable permanently until re-enabled
  python sb_control.py enable     — remove SB_DISABLE flag (takes effect next boot)
  python sb_control.py status     — show current state
  python sb_control.py log [N]    — tail last N lines of sb.log (default 50)
  python sb_control.py run        — show phase2_run.log (watchdog trace)
"""
import json, os, sys, urllib.request

sys.stdout.reconfigure(encoding='utf-8')
os.environ.pop('http_proxy', None); os.environ.pop('HTTP_PROXY', None)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def ubus(p):
    r = urllib.request.Request('http://192.168.1.1/ubus/',
        data=json.dumps(p).encode(), headers={'Content-Type':'application/json'})
    with opener.open(r, timeout=30) as x: return json.loads(x.read())

def login():
    d = ubus({'jsonrpc':'2.0','id':0,'method':'call',
        'params':['00000000000000000000000000000000','session','login',
                  {'username':'admin','password':'admin'}]})
    return d['result'][1]['ubus_rpc_session']

def write_empty(tok, path):
    r = ubus({'jsonrpc':'2.0','id':1,'method':'call',
        'params':[tok,'file','write',{'path':path,'data':'1'}]})
    return r.get('result')

def remove(tok, path):
    r = ubus({'jsonrpc':'2.0','id':1,'method':'call',
        'params':[tok,'file','remove',{'path':path}]})
    return r.get('result')

def exec_(tok, cmd, args=None):
    d = ubus({'jsonrpc':'2.0','id':1,'method':'call',
        'params':[tok,'file','exec',{'command':cmd,'params':args or []}]})
    r = d.get('result',[999,{}])
    return r[1].get('stdout','') if r[0]==0 and len(r)>1 else None

def cmd_stop(tok):
    write_empty(tok, '/etc/luci-uploads/SB_STOP')
    print('SB_STOP flag written. Watchdog will kill sing-box and clean up iptables within ~10s.')
    print('This is a one-shot stop — next boot sing-box starts again unless you also run "disable".')

def cmd_disable(tok):
    write_empty(tok, '/etc/luci-uploads/SB_STOP')
    write_empty(tok, '/etc/luci-uploads/SB_DISABLE')
    print('SB_STOP + SB_DISABLE set. Current instance stops in ~10s.')
    print('Subsequent boots will skip sing-box entirely.')

def cmd_enable(tok):
    remove(tok, '/etc/luci-uploads/SB_DISABLE')
    remove(tok, '/etc/luci-uploads/SB_STOP')
    print('SB_DISABLE removed. Reboot router (or it will start on next reboot).')

def cmd_status(tok):
    d = ubus({'jsonrpc':'2.0','id':1,'method':'call','params':[tok,'luci','getProcessList',{}]})
    procs = d['result'][1].get('processes', d['result'][1].get('result', []))
    sb = [p for p in procs if 'sing-box' in str(p.get('COMMAND',''))]
    print(f'sing-box processes: {len(sb)}')
    for p in sb: print(f'  PID={p.get("PID")} CMD={str(p.get("COMMAND",""))[:120]}')

    for p in ['/etc/luci-uploads/SB_STOP','/etc/luci-uploads/SB_DISABLE']:
        d = ubus({'jsonrpc':'2.0','id':1,'method':'call','params':[tok,'file','stat',{'path':p}]})
        r = d.get('result',[1,{}])
        print(f'  {p}: {"EXISTS" if r[0]==0 else "absent"}')

    d = ubus({'jsonrpc':'2.0','id':1,'method':'call','params':[tok,'system','info',{}]})
    r = d['result'][1]
    print(f'router uptime: {r["uptime"]}s, free mem: {r["memory"]["free"]//1024//1024}MB, load: {r["load"]}')

def cmd_log(tok, n=50):
    # try to read tail via small cat — may fail if log >~200KB
    out = exec_(tok, '/bin/cat', ['/etc/luci-uploads/sb.log'])
    if out is None:
        print('(sb.log too big, use "run" command or wait for rotation)')
        return
    lines = out.splitlines()
    for l in lines[-n:]: print(l[:250])

def cmd_run(tok):
    out = exec_(tok, '/bin/cat', ['/etc/luci-uploads/phase2_run.log'])
    print(out or '(empty)')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    tok = login()
    c = sys.argv[1]
    if c == 'stop':    cmd_stop(tok)
    elif c == 'disable': cmd_disable(tok)
    elif c == 'enable':  cmd_enable(tok)
    elif c == 'status':  cmd_status(tok)
    elif c == 'log':     cmd_log(tok, int(sys.argv[2]) if len(sys.argv)>2 else 50)
    elif c == 'run':     cmd_run(tok)
    else: print(__doc__)
