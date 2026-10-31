#!/usr/bin/env python3
"""Live brightness UI with injected readings/writes; no real level changes."""
import json,os,re,subprocess,sys,tempfile,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from strix_core.ui import Application,GLib
from strix_core.backend import Reader
from strix_core.live import LiveController
from strix_core.model import Storage
from strix_core import __version__

OUT=ROOT/'.preview/strix-core-qa'
OUT.mkdir(parents=True,exist_ok=True)
temporary=tempfile.TemporaryDirectory(dir=ROOT/'.preview')
tag=__version__.replace('.','')
actual_before=Reader().brightness()
class Readings(Reader):
    level=3
    def brightness(self):return self.level
reader=Readings();writes=[]
class Controller(LiveController):
    def apply(self,kind,value):
        assert kind=='brightness','No other action may execute in this test.'
        writes.append(value);reader.level=value
        event={'action':kind,'actual':value,'verified':True};self.history.append(event);return event
app=Application(reader,Storage(Path(temporary.name)),unique=True,test_mode=False)
app.set_application_id('io.github.hyprdodz.StrixCore.BrightnessCheck'+str(os.getpid()))
app.noticed_background=True
checks=[];errors=[];stage=0
def check(value,name):
    if not value:raise AssertionError(name)
    checks.append(name)
def tick():
    global stage
    try:
        w=app.window
        if stage==0:
            w.controller=Controller(reader);w.navigate('Lighting')
            check(w.brightness.get_value()==3,'cold start displays the current keyboard level')
            check('3 / 3' in w.brightness_reading.get_text(),'current level is explicitly labeled')
            check(not w.apply_brightness_button.get_sensitive(),'unchanged level needs no apply or authentication')
            supported=w.controller.supported
            def unexpected_probe(*_):raise AssertionError('No filesystem availability probe belongs in the drag handler')
            w.controller.supported=unexpected_probe
            for value in (2.9,2.7,2.4,2.2,1.8,1.6):
                w.brightness.set_value(value)
                check(abs(w.brightness.get_value()-value)<.001,'drag keeps fractional thumb position '+str(value))
            w.controller.supported=supported
            w.update_brightness(3)
            check(abs(w.brightness.get_value()-1.6)<.001,'unchanged sensor poll does not snap a drag position')
            w.brightness.set_value(3)
            reader.level=1;w.receive(reader.snapshot())
            check(w.brightness.get_value()==1,'external brightness changes update the slider')
            w.brightness.set_value(2)
            check(w.brightness_selection.pending,'manual selection creates a pending draft')
            check('Selected: 2 / 3' in w.brightness_reading.get_text(),'pending selection is distinct from current level')
            reader.level=0;w.receive(reader.snapshot())
            check(w.brightness_selection.actual==0 and w.brightness.get_value()==2,'current Off reading updates without erasing a pending draft')
            check('Current keyboard level: 0 / 3' in w.brightness_reading.get_text(),'Off is displayed as a real reading')
            w.apply_brightness_button.emit('clicked')
        elif stage==1:
            check(writes==[2],'Apply requests exactly the selected level through the fake controller')
            check(not w.brightness_selection.pending,'successful apply clears the pending draft')
            check(not w.apply_brightness_button.get_sensitive(),'applied selection no longer requests another apply')
            reader.level=3;w.receive(reader.snapshot())
            check(w.brightness.get_value()==3,'slider resumes tracking external changes after Apply')
            w.close_requested();reader.level=1;app.activate()
            check(w.brightness.get_value()==1,'reopen reads current brightness immediately')
            GLib.source_remove(w.timer);w.timer=GLib.timeout_add_seconds(60,w.poll)
            reader.level=2
            def fast_check():
                try:
                    check(w.brightness_selection.actual==2,'hardware reading follows within 150 ms without the sensor poll')
                    check(w.brightness.get_value()==2,'slider follows the independent brightness monitor')
                except Exception:errors.append(traceback.format_exc())
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(150,fast_check)
            a=w.stack.get_visible_child().get_vadjustment();a.set_value(a.get_upper()-a.get_page_size())
        elif stage==2:
            cs=json.loads(subprocess.run(['hyprctl','-j','clients'],capture_output=True,text=True,check=True).stdout)
            c=next(c for c in cs if c.get('pid')==os.getpid() and c.get('mapped'))
            address=c['address'];assert re.fullmatch('0x[0-9a-fA-F]+',address)
            subprocess.run(['hyprctl','dispatch',f'hl.dsp.focus({{window="address:{address}"}})'],capture_output=True,check=True)
            x,y=c['at'];width,height=c['size']
            subprocess.run(['grim','-g',f'{x},{y} {width}x{height}',str(OUT/('brightness-ui-'+tag+'.png'))],check=True)
            app.request_quit();return GLib.SOURCE_REMOVE
        stage+=1
    except Exception:
        errors.append(traceback.format_exc());app.window.busy.discard('control');app.request_quit();return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE
app.connect('activate',lambda *_:GLib.timeout_add(800,tick) if stage==0 else None)
def deadline():
    errors.append('Brightness check timed out');app.window.busy.discard('control');app.request_quit();return GLib.SOURCE_REMOVE
GLib.timeout_add_seconds(20,deadline)
app.run(['strix-core-brightness-check'])
result={'version':__version__,'checks':checks,'errors':errors,'fake_requests':writes,'real_brightness_before':actual_before,'real_brightness_after':Reader().brightness(),'real_brightness_writes':0}
(OUT/('brightness-'+tag+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));temporary.cleanup()
raise SystemExit(bool(errors))
