#!/usr/bin/env python3
"""Measure UI responsiveness and CPU while visible/hidden; simulated USB only."""
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from strix_core.ui import Application,GLib
from strix_core.backend import Reader
from strix_core.model import Storage
from strix_core.rgb_runtime import RGBSession
from strix_core.lighting import EFFECTS

(ROOT/'.preview/strix-core-qa').mkdir(parents=True,exist_ok=True)
temp=tempfile.TemporaryDirectory(dir=ROOT/'.preview')
reader=Reader()
app=Application(reader,Storage(Path(temp.name)),unique=True,test_mode=False)
app.set_application_id('io.github.hyprdodz.StrixCore.Profile'+str(os.getpid()))
class Device:
    def open(self):pass
    def take_control(self):pass
    def write_colors(self,colors):time.sleep(.016)  # USB wait belongs to the worker.
    def close(self):pass

phases=[];gaps=[];started=cpu=previous=0;phase=None;errors=[]
def record():
    elapsed=time.monotonic()-started
    phases.append({'phase':phase,'seconds':round(elapsed,2),
      'cpu_percent_one_core':round((time.process_time()-cpu)/elapsed*100,2),
      'main_loop_gap_p95_ms':round(sorted(gaps)[int(len(gaps)*.95)]*1000,2),
      'main_loop_gap_max_ms':round(max(gaps)*1000,2),
      'rss_mib':round(int(Path('/proc/self/status').read_text().split('VmRSS:')[1].split()[0])/1024,1),
      'rgb':app.window.controller.rgb_session.status()})

def heartbeat():
    global previous
    now=time.monotonic()
    if previous:gaps.append(now-previous)
    previous=now
    return GLib.SOURCE_CONTINUE

def change():
    global phase,started,cpu,previous,gaps
    if phase:
        record()
        if phase=='hidden':app.request_quit();return GLib.SOURCE_REMOVE
        app.window.close_requested();phase='hidden'
    else:
        w=app.window;w.navigate('Lighting');w.effect.set_selected(EFFECTS.index('Wave'))
        w.controller.rgb_session=RGBSession(reader,Path(temp.name),Device)
        w.controller.rgb_session.apply({'effect':'Wave','zones':['#ff0000','#00ff00','#0000ff','#ffffff'],'intensity':20})
        phase='visible Lighting'
    started=time.monotonic();cpu=time.process_time();previous=0;gaps=[]
    GLib.timeout_add(6000,change)
    return GLib.SOURCE_REMOVE

app.connect('activate',lambda *_:GLib.timeout_add(1500,change))
GLib.timeout_add(16,heartbeat)
def deadline():
    errors.append('Profile timed out');app.request_quit();return GLib.SOURCE_REMOVE
GLib.timeout_add_seconds(25,deadline)
app.run(['strix-core-profile'])
result={'phases':phases,'errors':errors,'physical_usb_used':False}
(ROOT/'.preview/strix-core-qa/app-performance.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2));temp.cleanup()
raise SystemExit(bool(errors) or len(phases)!=2)
