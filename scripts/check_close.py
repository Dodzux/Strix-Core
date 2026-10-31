#!/usr/bin/env python3
"""Send one real graceful close to our own empty GTK child; no user apps."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from strix_core.backend import Reader
from strix_core.live import LiveController

CHILD='''import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,Gio
a=Gtk.Application(application_id='io.github.hyprdodz.StrixCore.CloseCheck',flags=Gio.ApplicationFlags.NON_UNIQUE)
def show(*_):
 w=Gtk.ApplicationWindow(application=a,title='Strix Core empty close test')
 w.set_default_size(320,160);w.set_child(Gtk.Label(label='Empty test window. No unsaved work.'));w.present()
a.connect('activate',show)
a.run(['strix-core-close-check'])
'''
(ROOT/'.preview/strix-core-qa').mkdir(parents=True,exist_ok=True)
child=subprocess.Popen([sys.executable,'-c',CHILD],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    reader=Reader();deadline=time.monotonic()+8
    while child.pid not in {p['pid'] for p in reader.processes()}:
        if time.monotonic()>deadline or child.poll() is not None:raise RuntimeError('Our empty test window did not appear.')
        time.sleep(.1)
    result=LiveController(reader).apply('close',[child.pid])
    child.wait(timeout=8)
    assert child.returncode==0 and result['status']=='close-requested'
    result={'empty_child_closed_normally':True,'window_count':result['windows'],'user_applications_closed':0}
    (ROOT/'.preview/strix-core-qa/graceful-close-031.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
finally:
    if child.poll() is None:child.terminate();child.wait(timeout=5)
