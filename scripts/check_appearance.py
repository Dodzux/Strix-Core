#!/usr/bin/env python3
"""Isolated appearance/reopen regression; no desktop theme or hardware writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from collections import deque

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from strix_core.backend import Reader
from strix_core.model import Storage
from strix_core.ui import Application,GLib,Gtk

OUT=ROOT/'.preview/strix-core-qa/appearance-sync'
OUT.mkdir(parents=True,exist_ok=True)
temporary=tempfile.TemporaryDirectory(dir=ROOT/'.preview')
storage=Storage(Path(temporary.name))
preferences=storage.preferences();preferences.update(theme='Dark',start_page='Appearance')
storage.write('preferences.json',preferences)
class AccentReader(Reader):
    color='#4985d3'
    def accent(self):return self.color
reader=AccentReader()
app=Application(reader,storage,unique=True,test_mode=False)
app.set_application_id('io.github.hyprdodz.StrixCore.AppearanceCheck'+str(os.getpid()))
app.noticed_background=True
states=[];errors=[];steps=deque();switch=None;checks=[]

def check(value,name):
    checks.append({'check':name,'passed':bool(value)})

def toggle_fresh_accent():
    switch.set_active(False)
    reader.color='#bc6955'  # The telemetry snapshot still contains the old color.
    switch.set_active(True)
    check(app.window.accent==reader.color,'Enabling wallpaper accent reads its current saved color immediately')

def reopen_fresh_accent():
    before=(storage.root/'preferences.json').read_bytes()
    app.window.close_requested()
    reader.color='#55bc89'
    app.activate()
    check(app.window.accent==reader.color,'Reopening updates the saved accent before the next telemetry poll')
    check(switch.get_active() and switch.get_state(),'Reopening preserves the enabled wallpaper switch')
    check((storage.root/'preferences.json').read_bytes()==before,'Reopening does not rewrite saved preferences')

def disabled_reopen():
    switch.set_active(False)
    app.window.close_requested();reader.color='#d19855';app.activate()
    check(app.window.accent=='#86d6b3','A disabled wallpaper accent stays disabled after reopen')
    check(not switch.get_active() and not switch.get_state(),'Disabled switch position and state agree')

def cold_restart():
    global switch
    before=(storage.root/'preferences.json').read_bytes()
    enabled=storage.preferences()['wallpaper_accent']
    old=app.window;app.quitting=True
    old.close_requested();old.destroy();app.window=None;app.quitting=False
    app.activate();switch=app.window.wallpaper_accent_switch
    check(switch.get_active()==enabled and switch.get_state()==enabled,'Cold restart restores saved switch value '+str(enabled))
    check(app.window.accent==(reader.color if enabled else '#86d6b3'),'Cold restart resolves accent before telemetry '+str(enabled))
    check((storage.root/'preferences.json').read_bytes()==before,'Cold restart does not overwrite preferences '+str(enabled))

def enable_and_restart():
    switch.set_active(True);cold_restart()

def system_theme():
    themes=next(x for x in walk(app.window.pages['Appearance']) if isinstance(x,Gtk.DropDown))
    themes.set_selected(0);app.window.desktop.scheme=2;app.window.desktop_changed()
    check(app.window.resolved_theme=='Light','System light theme refresh preserves wallpaper sync')
    check(switch.get_active() and switch.get_state(),'Theme refresh preserves switch position and state')

def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:yield from walk(child);child=child.get_next_sibling()

def prepare_capture():
    # Tiling may make Appearance shorter than its content. Scroll our own
    # switch into view, then allow a frame before measuring screen bounds.
    w=app.window
    ok,bounds=switch.compute_bounds(w.pages['Appearance']);assert ok
    adjustment=w.stack.get_visible_child().get_vadjustment()
    center=bounds.get_y()+bounds.get_height()/2-adjustment.get_page_size()/2
    adjustment.set_value(max(adjustment.get_lower(),min(center,adjustment.get_upper()-adjustment.get_page_size())))

def capture(name):
    w=app.window
    cs=json.loads(subprocess.run(['hyprctl','-j','clients'],capture_output=True,text=True,check=True).stdout)
    c=next(c for c in cs if c.get('pid')==os.getpid() and c.get('mapped'))
    subprocess.run(['hyprctl','dispatch',f'hl.dsp.focus({{window="address:{c["address"]}"}})'],capture_output=True,check=True)
    active=json.loads(subprocess.run(['hyprctl','-j','activewindow'],capture_output=True,text=True,check=True).stdout)
    assert active['pid']==os.getpid()
    ok,bounds=switch.compute_bounds(w);assert ok
    x,y=c['at'];sx=x+round(bounds.get_x());sy=y+round(bounds.get_y())
    sw,sh=round(bounds.get_width()),round(bounds.get_height())
    subprocess.run(['grim','-g',f'{sx},{sy} {sw}x{sh}',str(OUT/(name+'.png'))],check=True)
    states.append({'stage':name,'accent':w.accent,'active':switch.get_active(),'state':switch.get_state(),
                   'checked':bool(switch.get_state_flags()&Gtk.StateFlags.CHECKED),
                   'theme':w.resolved_theme})

def start():
    global switch
    switches=[s for s in walk(app.window.pages['Appearance']) if isinstance(s,Gtk.Switch)]
    switch=switches[2]  # close-to-tray, restore, wallpaper-accent
    steps.extend([
        prepare_capture,
        lambda:capture('cold'),
        lambda:setattr(reader,'color','#d54a8d'),
        lambda:app.window.receive(reader.snapshot()),
        prepare_capture,
        lambda:capture('changed'),
        lambda:switch.set_active(False),
        lambda:switch.set_active(True),
        prepare_capture,
        lambda:capture('toggled'),
        toggle_fresh_accent,
        reopen_fresh_accent,
        prepare_capture,
        lambda:capture('reopened'),
        lambda:app.window.receive(reader.snapshot()),
        prepare_capture,
        lambda:capture('refreshed'),
        disabled_reopen,
        cold_restart,
        enable_and_restart,
        system_theme,
        prepare_capture,
        lambda:capture('system-light'),
        lambda:app.request_quit()])
    GLib.timeout_add(400,advance)
    return GLib.SOURCE_REMOVE

def advance():
    try:steps.popleft()()
    except Exception as error:
        errors.append(str(error));app.request_quit();return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE if steps else GLib.SOURCE_REMOVE
app.connect('activate',lambda *_:GLib.timeout_add(1000,start) if not states else None)
def deadline():
    errors.append('Appearance test timed out');app.request_quit();return GLib.SOURCE_REMOVE
GLib.timeout_add_seconds(20,deadline)
app.run(['strix-core-appearance-check'])
result={'states':states,'checks':checks,'errors':errors,'physical_usb_used':False}
(OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2));temporary.cleanup()
raise SystemExit(bool(errors) or any(not c['passed'] for c in checks))
