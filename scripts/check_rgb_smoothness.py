#!/usr/bin/env python3
"""Explicitly approved, 15-second, 20% RGB timing check; never store/flash.

Stops the installed app's output first, then restores the saved static desktop
color. Firmware four-zone breathing is enabled only inside this test until the
user confirms it physically. Other laptop controls are read, never written.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from strix_core.backend import Reader
from strix_core.rgb_runtime import RGBSession


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-15-second-dim-test',action='store_true')
    parser.add_argument('--approved-batched-apply',action='store_true',help='requires separate approval for the changed grouping')
    args=parser.parse_args()
    if not args.approved_15_second_dim_test:
        parser.error('Specific approval for this bounded hardware test is required.')
    (ROOT/'.preview/strix-core-qa').mkdir(parents=True,exist_ok=True)
    reader=Reader();before=reader.snapshot()
    subprocess.run(['/usr/bin/strix-core','--stop-lighting'],check=True,timeout=10)
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    with (runtime/'strix-core.rgb.lock').open('a') as lock:
        deadline=time.monotonic()+6
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>deadline:raise RuntimeError('The installed RGB worker has not stopped.')
                time.sleep(.05)
    session=RGBSession(reader);start=time.time();error=None;phases=[]
    try:
        with patch('strix_core.aura_device.NATIVE_BREATHING_VERIFIED',True), patch('strix_core.aura_device.BATCHED_APPLY_VERIFIED',args.approved_batched_apply):
            for effect in ('Breathing','Wave','Color cycle'):
                print('Five seconds at 20%: '+effect,flush=True)
                session.apply({'effect':effect,'zones':['#ff0000','#00ff00','#0000ff','#ffff00'],'intensity':20})
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if session.status()['error']:raise RuntimeError(session.status()['error'])
                    time.sleep(.05)
                phases.append(session.status())
    except Exception as failure:error=str(failure)
    finally:
        try:stop=session.stop()
        except Exception as failure:stop={'ok':False,'error':str(failure)}
    after=reader.snapshot();end=time.time()
    log=subprocess.run(['journalctl','-k','--since','@'+str(int(start)),'--until','@'+str(int(end)+1),'--no-pager','-o','cat'],capture_output=True,text=True,timeout=10)
    resets=[line for line in log.stdout.splitlines() if 'usb 3-1' in line and 'reset' in line.lower()]
    unchanged=all(before[k]==after[k] for k in ('profile','brightness','mux_value','dgpu_disabled')) and before['battery']['limit']==after['battery']['limit']
    result={'approved_seconds':15,'intensity':20,'batched_apply':args.approved_batched_apply,'phases':phases,'stop':stop,'error':error,'usb_resets':resets,'kernel_query_ok':log.returncode==0,'other_hardware_settings_unchanged':unchanged,'physical_smoothness':'awaiting user observation'}
    output=ROOT/('.preview/strix-core-qa/rgb-batched-hardware.json' if args.approved_batched_apply else '.preview/strix-core-qa/rgb-smoothness-hardware.json')
    output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 1 if error or not stop.get('ok') or resets or not unchanged else 0


if __name__=='__main__':raise SystemExit(main())
