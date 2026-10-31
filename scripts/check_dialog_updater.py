#!/usr/bin/env python3
"""Check confirmation styling and updater layout without running an upgrade."""
import json,os,re,subprocess,sys,tempfile,traceback
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from strix_core.ui import Application,Gtk,GLib
from strix_core.backend import Reader
from strix_core.model import Storage
from strix_core.live import LiveController

OUT=ROOT/'.preview/strix-core-qa'
OUT.mkdir(parents=True,exist_ok=True)
temporary=tempfile.TemporaryDirectory(dir=ROOT/'.preview')
reader=Reader();app=Application(reader,Storage(Path(temporary.name)),unique=True,test_mode=False)
app.set_application_id('io.github.hyprdodz.StrixCore.DialogUpdateCheck'+str(os.getpid()))
checks=[];errors=[];stage=0;dialog=None;requests=[];terminal=None
def check(value,name):
    if not value:raise AssertionError(name)
    checks.append(name)
def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:yield from walk(child);child=child.get_next_sibling()
def clients():return json.loads(subprocess.run(['hyprctl','-j','clients'],capture_output=True,text=True,check=True).stdout)
def focus(c):
    address=c['address'];assert re.fullmatch('0x[0-9a-fA-F]+',address)
    subprocess.run(['hyprctl','dispatch',f'hl.dsp.focus({{window="address:{address}"}})'],capture_output=True,check=True)
    a=json.loads(subprocess.run(['hyprctl','-j','activewindow'],capture_output=True,text=True,check=True).stdout)
    assert a['pid']==c['pid']
def capture(c,name):
    focus(c);x,y=c['at'];w,h=c['size']
    subprocess.run(['grim','-g',f'{x},{y} {w}x{h}',str(OUT/name)],check=True)
def open_dialog():
    app.window.perform_action('upgrade','sudo pacman -Syu')
    return next(d for d in Gtk.Window.list_toplevels() if isinstance(d,Gtk.MessageDialog) and d.get_transient_for()==app.window)
def tick():
    global stage,dialog,terminal
    try:
        w=app.window
        if stage==0:
            w.navigate('Update Center')
            w.run_control=lambda kind,value:requests.append((kind,value))
            dialog=open_dialog()
            check(dialog.get_modal(),'confirmation remains modal')
            check(all(dialog.has_css_class(c) for c in ('app-window','app-dialog')),'native dialog inherits app glass classes')
        elif stage==1:
            c=next(c for c in clients() if c.get('pid')==os.getpid() and c.get('floating') and c['size'][0]<750)
            capture(c,'dialog-glass-033.png')
            next(b for b in walk(dialog) if isinstance(b,Gtk.Button) and b.get_label()=='Cancel').emit('clicked')
        elif stage==2:
            check(not requests,'Cancel launches no upgrade')
            dialog=open_dialog()
        elif stage==3:
            c=next(c for c in clients() if c.get('pid')==os.getpid() and c.get('floating') and c['size'][0]<750)
            focus(c)
            next(b for b in walk(dialog) if isinstance(b,Gtk.Button) and b.get_label()=='Continue').emit('clicked')
        elif stage==4:
            check(requests==[('upgrade','sudo pacman -Syu')],'Continue forwards one intended request to an injected handler')
            with patch('strix_core.live.subprocess.Popen') as launch:
                LiveController(reader).upgrade();args=list(launch.call_args.args[0])
            check(args[0]=='/usr/bin/kitty' and '--class=cachy.update' in args,'launcher uses the same Kitty class as Super+U')
            check('sudo pacman -Syu' in args[-1],'real launcher retains the interactive pacman command')
            # Same window identity/layout, but a harmless test payload.
            args[1:1]=['--title','Strix Core updater layout test']
            args[-1]="printf '%s\\n' 'Strix Core updater presentation test.' 'No sudo or package operation is running.'; /usr/bin/sleep 3"
            with (OUT/'updater-layout-033.log').open('w') as log:
                terminal=subprocess.Popen(args,stdout=log,stderr=log,start_new_session=True)
        elif stage==5:
            c=next((c for c in clients() if c.get('pid')==terminal.pid and c.get('mapped')),None)
            if not c:return GLib.SOURCE_CONTINUE
            check(c['class']=='cachy.update','test terminal maps with the desktop updater identity')
            check(c['floating'],'existing desktop rule makes the updater float')
            check(c['size']==[1000,640],'updater uses the existing 1000 by 640 layout')
            capture(c,'updater-floating-033.png')
            icon=Path.home()/'.config/quickshell/assets/pacman.svg'
            taskbar=Path.home()/'.config/quickshell/bar/Taskbar.qml'
            check(icon.is_file() and 'key: "cachy"' in taskbar.read_text() and 'assets/pacman.svg' in taskbar.read_text(),'existing desktop pacman icon mapping matches this class')
        elif stage==6:
            if terminal.poll() is None:return GLib.SOURCE_CONTINUE
            check(terminal.poll() is not None,'only the test updater terminal closes normally')
            app.request_quit();return GLib.SOURCE_REMOVE
        stage+=1
    except Exception:
        errors.append(traceback.format_exc());app.request_quit();return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE
app.connect('activate',lambda *_:GLib.timeout_add(500,tick))
def deadline():
    errors.append('Dialog/updater regression timed out');app.request_quit();return GLib.SOURCE_REMOVE
GLib.timeout_add_seconds(25,deadline)
app.run(['strix-core-dialog-updater-check'])
if terminal and terminal.poll() is None:terminal.terminate();terminal.wait(timeout=5)
result={'checks':checks,'errors':errors,'real_package_upgrade_run':False,'physical_usb_used':False}
(OUT/'dialog-updater-033.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2));temporary.cleanup()
raise SystemExit(bool(errors))
