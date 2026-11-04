"""Run a serialized vanilla blueprint on the native controller model."""
import argparse
import hashlib
import json
from pathlib import Path

from native_simulator import NativeLogicSimulator


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def resolve(folder,name):
    folder=Path(folder)
    if name.startswith('#'):return [int(name[1:])]
    if name.startswith('key.'):
        terminal=load(folder/'terminal-map.json')
        matches=[k['controllerId'] for k in terminal['keys'] if k['key']==name[4:]]
        if len(matches)!=1:raise ValueError('Unknown keyboard key '+name)
        return matches
    for prefix,filename in (('clock.','clock-map.json'),('service.','service-clock-map.json')):
        if name.startswith(prefix):return [load(folder/filename)['names'][name[len(prefix):]]]
    if name in ('run','reset','manual_capture','manual_commit') and (folder/'clock-map.json').exists():
        return [load(folder/'clock-map.json')['names'][name]]
    ports=load(folder/'port-map.json')
    return [b['controllerId'] for b in ports[name]['bits']]


def run(folder,*,ticks,stimuli=(),watches=(),input_mode='controller',initial_snapshot=None):
    folder=Path(folder);raw=(folder/'blueprint.json').read_bytes()
    sim=NativeLogicSimulator(json.loads(raw),input_mode=input_mode)
    if initial_snapshot:sim.restore(initial_snapshot)
    if input_mode!='controller' and stimuli:raise ValueError('Scripted future input requests require controller input mode')
    for row in stimuli:
        requested=row['tick']
        if type(requested) is not int or requested<sim.time:raise ValueError('Input action precedes snapshot time')
        for name,value in row['set'].items():
            ids=resolve(folder,name)
            if not isinstance(value,int) or not 0<=value<1<<len(ids):raise ValueError('Input value does not fit '+name)
            for bit,identifier in enumerate(ids):
                sim.request_input(identifier,bool(value&(1<<bit)),at_tick=requested+1)
    for name in watches:sim.watch(name,resolve(folder,name))
    sim.advance(ticks)
    result=dict(model=sim.describe(),blueprint_sha256=hashlib.sha256(raw).hexdigest(),
                tick=sim.time,nominal_seconds=sim.nominal_seconds,stimulus=stimuli,
                signals={name:hex(sim.read_ids(resolve(folder,name))) for name in watches},
                trace=[dict(row,value=hex(row['value'])) for row in sim.trace])
    return sim,result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('blueprint',type=Path,nargs='?',help='Export directory containing blueprint.json')
    parser.add_argument('--record',type=Path,help='Alternatively read folder from an export-record JSON')
    parser.add_argument('--ticks',type=int,required=True,help='Number of native updates, not host seconds')
    parser.add_argument('--inputs',type=Path,help='JSON [{"tick":0,"set":{"reset":1}}, ...]; action commits tick+1')
    parser.add_argument('--watch',action='append',default=[],help='Port, clock.name, service.name, key.A or #controllerID')
    parser.add_argument('--output',type=Path,required=True,help='JSON report with exact tick trace')
    parser.add_argument('--snapshot-in',type=Path)
    parser.add_argument('--snapshot-out',type=Path)
    args=parser.parse_args()
    if bool(args.blueprint)==bool(args.record):parser.error('Choose one blueprint directory or --record')
    folder=args.blueprint or Path(load(args.record)['folder'])
    sim,result=run(folder,ticks=args.ticks,stimuli=load(args.inputs) if args.inputs else (),watches=args.watch,
                   initial_snapshot=load(args.snapshot_in) if args.snapshot_in else None)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    if args.snapshot_out:
        args.snapshot_out.parent.mkdir(parents=True,exist_ok=True)
        args.snapshot_out.write_text(json.dumps(sim.snapshot(),indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('trace','stimulus')},indent=2))


if __name__=='__main__':main()
