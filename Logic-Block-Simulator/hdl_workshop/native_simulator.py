"""Authoritative Scrap Mechanic logic/timer model, with sparse tick evaluation.

The native controller pass computes from committed old states, then publishes
all results. Timers OR samples into a 304-byte history, peek bit zero, and
shift. This implementation skips unchanged updates without skipping that
history. It models the inspected 1.0.6.889 controller path, not rendering,
physics cost, network interpolation or a measured wall-clock tick rate.
"""
from dataclasses import dataclass
import heapq
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from circuit_core import GATE_UUID,SWITCH_UUID,BUTTON_UUID,MODES
from blueprint_supports import TIMER_UUID

MODEL_VERSION='native-logic-2'
NATIVE_SHA256='cf403cf9d3e465e05298c8e6082f4d664c32140ebcc4696a64200e367abb530d'
BUFFER_BITS=2432
BUFFER_MASK=(1<<BUFFER_BITS)-1


def timer_index(seconds,ticks):
    if type(seconds) is not int or type(ticks) is not int or not 0<=seconds<=255 or not 0<=ticks<=255:
        raise ValueError('Native timer fields must be unsigned bytes')
    value=40*seconds+ticks
    if value>=BUFFER_BITS:raise ValueError('Timer delay exceeds its native 2432-bit allocation')
    return value


def legacy_timer_fields(seconds,ticks,loader_version):
    """Only apply the native legacy conversion when its version is explicit.

    The loader's version-argument provenance is not established from the outer
    blueprint JSON version. Do not silently infer this argument from that key.
    """
    timer_index(seconds,ticks)
    if loader_version is not None and type(loader_version) is not int:raise ValueError('Explicit loader version must be an integer')
    if loader_version is not None and loader_version<3:
        if ticks==40:
            if seconds<59:seconds,ticks=seconds+1,1
        else:ticks=(ticks+1)&255
    timer_index(seconds,ticks)
    return seconds,ticks


@dataclass
class TimerState:
    seconds:int
    ticks:int
    bits:int=0
    future:bool=False
    held:bool=False
    time:int=0
    version:int=0

    @property
    def delay(self):return timer_index(self.seconds,self.ticks)

    def advance_to(self,tick):
        count=tick-self.time
        if count<0:raise ValueError('Timer time cannot run backwards')
        if not count:return self.future
        delay=self.delay
        self.future=bool((self.bits>>(count-1))&1) or (self.held and count>delay)
        self.bits >>= count
        if self.held and delay:
            low=max(0,delay-count)
            self.bits |= ((1<<delay)-1)^((1<<low)-1)
        self.time=tick
        return self.future

    def next_change(self,current):
        # For a high parent, the first N outputs are existing history and all
        # later samples are high. With low/no parent, ALL old bits drain.
        if self.held:
            mask=(1<<self.delay)-1
            different=((~self.bits)&mask) if current else (self.bits&mask)
            if different:return (different&-different).bit_length()
            return None if current else self.delay+1
        if current:
            different=(~self.bits)&BUFFER_MASK
            return (different&-different).bit_length() if different else BUFFER_BITS+1
        return (self.bits&-self.bits).bit_length() if self.bits else None


class NativeLogicSimulator:
    """A stateful native-update simulator for serialized vanilla controllers.

    ``advance(n)`` performs n logical updates, skipping constant intervals.
    ``step()`` jumps to the next pending observable update. ``set`` queues a
    requested input level for the next controller commit by default; use
    ``force_input`` for an explicitly already-committed test stimulus.

    Configurations are assumed applied before tick zero. JSON active states
    are used with empty timer histories; exact import/RPC initialization
    ordering remains a separate uncertainty. Full snapshots are explicit.
    """
    def __init__(self,blueprint,*,input_mode='controller',tick_hz=40,
                 timer_loader_version=None,connection_policy='strict',timer_history=None):
        if input_mode not in ('controller','committed'):raise ValueError('Unknown input timing mode')
        if connection_policy not in ('strict','raw'):raise ValueError('Unknown connection policy')
        if tick_hz<=0:raise ValueError('Positive nominal tick rate required')
        supported={GATE_UUID,SWITCH_UUID,BUTTON_UUID,TIMER_UUID}
        all_parts=[p for b in blueprint.get('bodies',[]) for p in b.get('childs',[]) if 'controller' in p]
        self.nodes={};self.kinds={};self.input_types={};self.values={};self.parents={};self.children={}
        self.modes={};self.timer_states={};self.input_mode=input_mode;self.tick_hz=tick_hz
        self.connection_policy=connection_policy;self.timer_loader_version=timer_loader_version
        all_ids={}
        for part in all_parts:
            identifier=part['controller'].get('id')
            if type(identifier) is not int:raise ValueError('Controller IDs must be integers')
            if identifier in all_ids:raise ValueError(f'Duplicate controller ID {identifier}')
            all_ids[identifier]=part
            if part['shapeId'] not in supported:continue
            c=part['controller'];self.nodes[identifier]=part
            self.parents[identifier]=[];self.children[identifier]=[]
            if part['shapeId'] in (SWITCH_UUID,BUTTON_UUID):
                self.kinds[identifier]='INPUT'
                self.input_types[identifier]='button' if part['shapeId']==BUTTON_UUID else 'lever'
            elif part['shapeId']==TIMER_UUID:
                self.kinds[identifier]='TIMER'
                seconds,ticks=legacy_timer_fields(c.get('seconds',0),c.get('ticks',0),timer_loader_version)
                bits=(timer_history or {}).get(identifier,0)
                if isinstance(bits,(bytes,bytearray)):
                    if len(bits)!=304:raise ValueError('Native timer history must contain exactly 304 bytes')
                    bits=int.from_bytes(bits,'little')
                if type(bits) is not int or bits<0 or bits>BUFFER_MASK:raise ValueError('Invalid native timer history')
                self.timer_states[identifier]=TimerState(seconds,ticks,bits=bits)
            else:
                mode=c.get('mode',0)
                if type(mode) is not int or mode not in MODES.values():raise ValueError(f'Unsupported logic mode {mode}')
                self.modes[identifier]=mode
                self.kinds[identifier]=next(k for k,v in MODES.items() if v==mode)
            # The button-local JSON hooks are no-ops; constructor output is
            # zero. Logic and lever JSON loaders explicitly restore active.
            self.values[identifier]=False if part['shapeId']==BUTTON_UUID else bool(c.get('active',False))
        edges=set()
        for source,part in all_ids.items():
            for link in part['controller'].get('controllers') or []:
                target=link['id']
                if type(target) is not int:raise ValueError('Connection IDs must be integers')
                if source not in self.nodes or target not in self.nodes:
                    raise ValueError(f'Wired unsupported/missing controller: {source} -> {target}')
                if (source,target) in edges:
                    if connection_policy=='strict':raise ValueError(f'Duplicate connection {source} -> {target}')
                    continue
                if connection_policy=='strict':
                    if source!=target and (target,source) in edges:raise ValueError('Native loader rejects reciprocal links; use a relay')
                    maximum=1 if self.kinds[target]=='TIMER' else 255
                    if self.kinds[target]=='INPUT':raise ValueError('Physical inputs cannot have logic parents')
                    if len(self.parents[target])>=maximum or len(self.children[source])>=255:
                        raise ValueError('Native controller connection capacity exceeded')
                edges.add((source,target));self.parents[target].append(source);self.children[source].append(target)
        topology=[(i,self.nodes[i]['shapeId'],self.parents[i] if i in self.timer_states else sorted(self.parents[i]))
                  for i in sorted(self.nodes)]
        self.graph_sha256=hashlib.sha256(json.dumps(topology,separators=(',',':')).encode()).hexdigest()
        self.time=0;self._heap=[];self._serial=0;self._gate_events=set();self._input_requests={}
        self._watches={};self.trace=[];self._observers=[]
        self.stats=dict(gate_evaluations=0,timer_evaluations=0,commits=0,discarded_events=0)
        for identifier in self.nodes:
            if self.kinds[identifier]=='TIMER':
                state=self.timer_states[identifier]
                state.held=bool(self.parents[identifier] and self.values[self.parents[identifier][0]])
                self._reschedule_timer(identifier)
            elif self.kinds[identifier]!='INPUT':self._schedule_gate(identifier,1)

    @property
    def nominal_seconds(self):return self.time/self.tick_hz

    @property
    def delays(self):return {i:t.delay+1 for i,t in self.timer_states.items()}

    def _valid(self,event):
        at,_,identifier,kind,version=event
        if kind=='gate':return (identifier,at) in self._gate_events
        if kind=='input':return (identifier,at) in self._input_requests
        return self.timer_states[identifier].version==version

    @property
    def events(self):
        while self._heap and not self._valid(self._heap[0]):
            heapq.heappop(self._heap);self.stats['discarded_events']+=1
        return self._heap

    def _push(self,identifier,at,kind,version=0):
        self._serial+=1;heapq.heappush(self._heap,(at,self._serial,identifier,kind,version))

    def _schedule_gate(self,identifier,at):
        if (identifier,at) not in self._gate_events:
            self._gate_events.add((identifier,at));self._push(identifier,at,'gate')

    def _reschedule_timer(self,identifier):
        state=self.timer_states[identifier];state.version+=1
        delay=state.next_change(self.values[identifier])
        if delay is not None:self._push(identifier,self.time+delay,'timer',state.version)

    def evaluate(self,identifier):
        values=[self.values[p] for p in self.parents[identifier]]
        if not values:return False
        mode=self.modes[identifier]
        if mode==0:return all(values)
        if mode==1:return any(values)
        if mode==2:return bool(sum(values)&1)
        if mode==3:return not all(values)
        if mode==4:return not any(values)
        return not bool(sum(values)&1)

    def _publish(self,updates,*,timer_due=(),phase='commit'):
        changed={i:bool(value) for i,value in updates.items() if self.values[i]!=bool(value)}
        dirty=set(timer_due)
        for identifier in changed:
            for child in self.children[identifier]:
                if self.kinds[child]=='TIMER':
                    self.timer_states[child].advance_to(self.time);dirty.add(child)
        self.values.update(changed)
        for identifier in changed:
            for child in self.children[identifier]:
                if self.kinds[child] not in ('TIMER','INPUT'):self._schedule_gate(child,self.time+1)
        for identifier in dirty:
            state=self.timer_states[identifier]
            state.held=bool(self.parents[identifier] and self.values[self.parents[identifier][0]])
            self._reschedule_timer(identifier)
        if changed:
            self.stats['commits']+=len(changed)
            for name,watch in self._watches.items():
                if watch['ids']&changed.keys():
                    value=self.read_ids(watch['bits'])
                    if value!=watch['last']:
                        watch['last']=value
                        self.trace.append(dict(tick=self.time,channel=name,value=value,phase=phase))
            for callback in self._observers:callback(self,changed,phase)
        return changed

    def step(self):
        if not self.events:return False
        at=self._heap[0][0];due=[]
        while self._heap and self._heap[0][0]==at:
            event=heapq.heappop(self._heap)
            if self._valid(event):due.append(event)
        updates={};timers=[]
        for _,_,identifier,kind,_ in due:
            if kind=='input':updates[identifier]=self._input_requests.pop((identifier,at))
            elif kind=='gate':
                self._gate_events.remove((identifier,at))
                updates[identifier]=self.evaluate(identifier);self.stats['gate_evaluations']+=1
            else:
                updates[identifier]=self.timer_states[identifier].advance_to(at)
                timers.append(identifier);self.stats['timer_evaluations']+=1
        self.time=at;self._publish(updates,timer_due=timers)
        return True

    def advance(self,updates):
        if type(updates) is not int or updates<0:raise ValueError('Advance takes a nonnegative number of native updates')
        end=self.time+updates
        while self.events and self.events[0][0]<=end:self.step()
        self.time=end

    def tick(self):self.advance(1)

    def request_input(self,identifier,value,*,at_tick=None):
        if self.kinds[identifier]!='INPUT':raise ValueError('Only switch/button levels can be requested')
        at=self.time+1 if at_tick is None else at_tick
        if type(at) is not int or at<=self.time:raise ValueError('Input requests target a future commit tick')
        key=(identifier,at)
        if key not in self._input_requests:self._push(identifier,at,'input')
        self._input_requests[key]=bool(value)

    def force_input(self,identifier,value):
        if self.kinds[identifier]!='INPUT':raise ValueError('Only switch/button levels can be driven externally')
        return self._publish({identifier:value},phase='committed-stimulus')

    def set(self,identifier,value):
        if self.input_mode=='controller':return self.request_input(identifier,value)
        return self.force_input(identifier,value)

    def read_ids(self,bits):return sum(int(self.values[i])<<j for j,i in enumerate(bits))

    def read_port(self,port):return self.read_ids([b['controllerId'] for b in port['bits']])

    def watch(self,name,bits):
        if isinstance(bits,dict):bits=[b['controllerId'] for b in bits['bits']]
        bits=list(bits);value=self.read_ids(bits)
        self._watches[name]=dict(bits=bits,ids=set(bits),last=value)
        self.trace.append(dict(tick=self.time,channel=name,value=value,phase='initial'))

    def observe(self,callback):self._observers.append(callback)

    def timer_history(self,identifier):
        state=self.timer_states[identifier];state.advance_to(self.time)
        return state.bits.to_bytes(BUFFER_BITS//8,'little')

    def apply_timer_settings(self,identifier,seconds,ticks):
        timer_index(seconds,ticks);state=self.timer_states[identifier];state.advance_to(self.time)
        if (state.seconds,state.ticks)==(seconds,ticks):return False
        state.seconds,state.ticks=seconds,ticks;state.bits=0
        self._publish({identifier:False},phase='settings-apply')
        self._reschedule_timer(identifier)
        return True

    def apply_gate_mode(self,identifier,mode):
        if identifier not in self.modes or type(mode) is not int or mode not in MODES.values():
            raise ValueError('Applied gate mode must be 0..5 on a logic controller')
        if self.modes[identifier]==mode:return False
        self.modes[identifier]=mode
        self.kinds[identifier]=next(k for k,v in MODES.items() if v==mode)
        self._schedule_gate(identifier,self.time+1)
        return True

    def reset_timer(self,identifier):
        """Explicit native controller-reset operation, NOT a wired RESET input."""
        state=self.timer_states[identifier];state.advance_to(self.time)
        if not (self.values[identifier] or state.bits):return False
        state.bits=0;self._publish({identifier:False},phase='controller-reset');self._reschedule_timer(identifier)
        return True

    def snapshot(self):
        return dict(model=MODEL_VERSION,graph_sha256=self.graph_sha256,tick=self.time,values=dict(self.values),modes=dict(self.modes),
            timers={i:dict(seconds=s.seconds,ticks=s.ticks,history=self.timer_history(i).hex(),future=s.future)
                    for i,s in self.timer_states.items()},
            input_requests=[dict(id=i,tick=t,value=v) for (i,t),v in self._input_requests.items()],
            input_mode=self.input_mode,tick_hz=self.tick_hz)

    def restore(self,snapshot):
        if snapshot.get('model')!=MODEL_VERSION or snapshot.get('graph_sha256')!=self.graph_sha256:
            raise ValueError('Snapshot model/topology differs from this circuit')
        if set(map(int,snapshot['values']))!=set(self.values):raise ValueError('Snapshot controller set differs')
        if set(map(int,snapshot['timers']))!=set(self.timer_states):raise ValueError('Snapshot timer set differs')
        if set(map(int,snapshot['modes']))!=set(self.modes):raise ValueError('Snapshot gate set differs')
        if type(snapshot['tick']) is not int or snapshot['tick']<0:raise ValueError('Invalid snapshot update number')
        self.time=snapshot['tick'];self.values={int(i):bool(v) for i,v in snapshot['values'].items()}
        self._heap=[];self._gate_events=set();self._input_requests={}
        for key,mode in snapshot.get('modes',self.modes).items():
            if type(mode) is not int or mode not in MODES.values():raise ValueError('Invalid snapshot gate mode')
            i=int(key);self.modes[i]=mode;self.kinds[i]=next(k for k,v in MODES.items() if v==mode)
        for key,record in snapshot['timers'].items():
            i=int(key);state=self.timer_states[i]
            timer_index(record['seconds'],record['ticks'])
            state.seconds,state.ticks=record['seconds'],record['ticks']
            history=bytes.fromhex(record['history'])
            if len(history)!=304:raise ValueError('Invalid snapshot timer buffer')
            state.bits=int.from_bytes(history,'little');state.future=record['future'];state.time=self.time
        for i in self.nodes:
            if i in self.timer_states:
                state=self.timer_states[i];state.held=bool(self.parents[i] and self.values[self.parents[i][0]])
                self._reschedule_timer(i)
            elif self.kinds[i]!='INPUT':self._schedule_gate(i,self.time+1)
        for r in snapshot['input_requests']:self.request_input(r['id'],r['value'],at_tick=r['tick'])
        self.input_mode=snapshot['input_mode'];self.tick_hz=snapshot['tick_hz']
        for name,watch in self._watches.items():
            watch['last']=self.read_ids(watch['bits'])
            self.trace.append(dict(tick=self.time,channel=name,value=watch['last'],phase='restore'))

    def describe(self):
        return dict(model=MODEL_VERSION,native_artifact_sha256=NATIVE_SHA256,
            semantics='Authoritative compute-all/commit-all; sampled OR/peek/shift timers',
            configured_timer_latency='N+1 updates from committed input transition',input_mode=self.input_mode,
            nominal_tick_hz=self.tick_hz,nodes=len(self.nodes),timers=len(self.timer_states),
            initialization='Applied configuration; JSON active states; empty timer history unless explicit',
            limits=['Importer/RPC startup order not reproduced','No rendering, physics cost or network interpolation',
                    'Button-local load starts OFF; outer loader side effects remain untraced',
                    'Requested levels commit next tick; actual UI/network arrival time is external',
                    'Nominal seconds are not measured game wall time'],stats=dict(self.stats))
