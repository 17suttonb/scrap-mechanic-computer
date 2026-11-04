"""Deliberately slow, full-controller compute/commit reference simulator.

No event queue, timer edge prediction or packed-history shortcut is shared
with native_simulator. The bytewise timer oracle follows saved native code.
Use small differential tests; use NativeLogicSimulator for complete computers.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from circuit_core import GATE_UUID,SWITCH_UUID,BUTTON_UUID
from blueprint_supports import TIMER_UUID
from native_timer_reference import TimerOracle


class NativeReferenceSimulator:
    def __init__(self,blueprint,*,input_mode='controller',timer_history=None):
        self.nodes={p['controller']['id']:p for b in blueprint['bodies'] for p in b['childs']
                    if p['shapeId'] in (GATE_UUID,SWITCH_UUID,BUTTON_UUID,TIMER_UUID)}
        self.parents={i:[] for i in self.nodes}
        self.values={i:(False if p['shapeId']==BUTTON_UUID else bool(p['controller'].get('active',False)))
                     for i,p in self.nodes.items()}
        self.modes={i:p['controller'].get('mode',0) for i,p in self.nodes.items() if p['shapeId']==GATE_UUID}
        self.inputs={i for i,p in self.nodes.items() if p['shapeId'] in (SWITCH_UUID,BUTTON_UUID)}
        self.timers={}
        for i,p in self.nodes.items():
            if p['shapeId']==TIMER_UUID:
                history=(timer_history or {}).get(i,bytes(304))
                if type(history) is int:history=history.to_bytes(304,'little')
                c=p['controller'];self.timers[i]=TimerOracle(c.get('seconds',0),c.get('ticks',0),
                    active=self.values[i],history=history)
            for edge in p['controller'].get('controllers') or []:
                if edge['id'] not in self.nodes:raise ValueError('Unsupported reference connection')
                if i not in self.parents[edge['id']]:self.parents[edge['id']].append(i)
        self.time=0;self.input_mode=input_mode;self.requests={}

    def step(self):
        old=dict(self.values);result={}
        for i in self.nodes:
            if i in self.inputs:continue
            if i in self.timers:
                result[i]=self.timers[i].compute(old[p] for p in self.parents[i])
                continue
            count=len(self.parents[i]);ones=sum(old[p] for p in self.parents[i]);mode=self.modes[i]
            if not count:result[i]=False
            elif mode==0:result[i]=ones==count
            elif mode==1:result[i]=ones>0
            elif mode==2:result[i]=ones%2==1
            elif mode==3:result[i]=ones<count
            elif mode==4:result[i]=ones==0
            elif mode==5:result[i]=ones%2==0
            else:raise ValueError('Unsupported reference gate mode')
        self.time+=1
        for i in self.inputs:
            if (i,self.time) in self.requests:result[i]=self.requests.pop((i,self.time))
        # No controller has seen result while computing above.
        for timer in self.timers.values():timer.commit()
        self.values.update(result)
        return True

    def advance(self,n):
        for _ in range(n):self.step()

    def request_input(self,i,value,*,at_tick=None):
        if i not in self.inputs:raise ValueError('Not a physical input')
        self.requests[i,self.time+1 if at_tick is None else at_tick]=bool(value)

    def force_input(self,i,value):
        if i not in self.inputs:raise ValueError('Not a physical input')
        self.values[i]=bool(value)

    def set(self,i,value):
        if self.input_mode=='controller':self.request_input(i,value)
        else:self.force_input(i,value)

    def apply_timer_settings(self,i,seconds,ticks):
        changed=self.timers[i].apply_settings(seconds,ticks);self.values[i]=self.timers[i].active
        return changed

    def reset_timer(self,i):
        changed=self.timers[i].reset();self.values[i]=self.timers[i].active
        return changed

    def apply_gate_mode(self,i,mode):
        changed=self.modes[i]!=mode;self.modes[i]=mode;return changed

    def timer_history(self,i):return bytes(self.timers[i].history)
