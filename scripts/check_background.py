"""Real tray/CLI/window lifecycle with an injected RGB device, never physical USB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from strix_core.ui import Application,GLib
from strix_core.backend import Reader
from strix_core.model import Storage
from strix_core.rgb_runtime import RGBSession

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'.preview/strix-core-qa'
OUT.mkdir(parents=True,exist_ok=True)
temp=tempfile.TemporaryDirectory(dir=ROOT/'.preview')
reader=Reader();before=reader.snapshot()
app=Application(reader,Storage(Path(temp.name)),unique=True,test_mode=False)
identity='io.github.hyprdodz.StrixCore.Check'+str(os.getpid())
app.set_application_id(identity)  # Never forward tests to the user's running app.
FORWARD='''import sys
from pathlib import Path
from strix_core.ui import Application
from strix_core.backend import Reader
from strix_core.model import Storage
a=Application(Reader(),Storage(Path(sys.argv[1])),unique=True,test_mode=False)
a.set_application_id(sys.argv[2])
raise SystemExit(a.run(['strix-core-check']+sys.argv[3:]))
'''
def forward(*args):
    return subprocess.Popen([sys.executable,'-c',FORWARD,temp.name,identity,*args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
errors=[];checks=[];child=None;stage=0;initial_frames=0
class Device:
    def __init__(self):self.frames=[];self.closed=False
    def open(self):pass
    def take_control(self):pass
    def write_colors(self,colors):self.frames.append(colors)
    def close(self):self.closed=True
fake=Device()
def check(value,name):
    if not value:raise AssertionError(name)
    checks.append(name)
def tick():
    global stage,child,initial_frames
    try:
        w=app.window
        if stage==0:
            check(w is not None and w.get_visible(),'native live window created')
            check(app.tray.available,'tray registered with the real desktop watcher')
            check(bool(app.tray.pixmap) and len(app.tray.pixmap[0][2]) == 48*48*4, 'Wing tray icon includes an ARGB pixmap fallback')
            session=RGBSession(reader,Path(temp.name),lambda:fake)
            w.controller.rgb_session=session
            session.apply({'effect':'Wave','zones':['#ff0000','#00ff00','#0000ff','#ffffff'],'intensity':20})
            initial_frames=len(fake.frames)
            check(w.close_requested() is True,'close minimizes instead of quitting')
            check(not w.get_visible() and w.alive,'hidden window remains alive')
            stage=1;return GLib.SOURCE_CONTINUE
        if stage==1:
            check(len(fake.frames)>initial_frames,'RGB frames continue while window is hidden')
            child=forward('--page','Lighting')
            stage=2;return GLib.SOURCE_CONTINUE
        if stage==2:
            check(child.poll()==0,'normal CLI forwards to the existing application')
            check(w.get_visible(),'second launcher restores the hidden window')
            check(w.stack.get_visible_child_name()=='Lighting','forwarded page selected')
            previous_events=len(w.controller.history)
            w.busy.add('control')
            w.cycle_lighting(); w.cycle_lighting()
            check(w.pending_lighting is not None and len(w.controller.history)==previous_events,'rapid AURA requests coalesce while a control is busy')
            w.busy.discard('control')
            w.drain_rgb_request()
            child=forward('--cycle-lighting')
            stage=3;return GLib.SOURCE_CONTINUE
        if stage==3:
            check(child.poll()==0,'AURA CLI forwarded successfully')
            check(w.controller.history[-1]['action']=='lighting','AURA command applied through the existing RGB stream')
            check(w.controller.history[-1]['value']['effect']==w.effect.get_selected_item().get_string(),'latest queued effect matches device output selection')
            check((Path(temp.name)/'lighting.json').is_file(),'applied settings persist')
            dest=app.tray.connection.get_unique_name()
            child=subprocess.Popen(['gdbus','call','--session','--dest',dest,'--object-path','/MenuBar','--method','com.canonical.dbusmenu.GetLayout','0','int32 -1','[]'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            stage=4;return GLib.SOURCE_CONTINUE
        if stage==4:
            check(child.poll()==0,'real DBusMenu GetLayout request succeeds')
            output,error=child.communicate()
            check(b'Quit Strix Core' in output and b'Next lighting effect' in output,'tray exposes lighting and quit controls')
            after=reader.snapshot()
            check(all(before[k]==after[k] for k in ('profile','brightness','mux_value','dgpu_disabled')) and before['battery']['limit']==after['battery']['limit'],'real hardware settings unchanged')
            w.navigate('Lighting')
            cs=json.loads(subprocess.run(['hyprctl','-j','clients'],capture_output=True,text=True,check=True).stdout)
            c=next(c for c in cs if c['pid']==os.getpid() and c['mapped'])
            x,y=c['at'];width,height=c['size']
            subprocess.run(['grim','-g',f'{x},{y} {width}x{height}',str(OUT/'background-lighting-ui.png')],check=True)
            check(w.lighting_footer.get_mapped(),'Lighting action bar remains visible')
            app.request_quit()
            check(fake.closed and not w.controller.rgb_session,'explicit Quit stops the RGB worker')
            return GLib.SOURCE_REMOVE
    except Exception:
        errors.append(traceback.format_exc())
        print(errors[-1],file=sys.stderr)
        app.quitting=True
        if app.window:
            app.window.busy.discard('control')
        app.request_quit()
        return GLib.SOURCE_REMOVE
started = [False]
def start(*_):
    if not started[0]:
        started[0] = True
        GLib.timeout_add(1100,tick)
app.connect('activate',start)
def deadline():
    errors.append('Background integration exceeded 30 seconds')
    app.window.busy.discard('control')
    app.request_quit()
    return GLib.SOURCE_REMOVE
GLib.timeout_add_seconds(30,deadline)
app.run(['strix-core-background-check'])
result={'checks':checks,'errors':errors,'physical_usb_used':False}
(OUT/'background-integration.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
temp.cleanup()
raise SystemExit(1 if errors or len(checks)<12 else 0)
