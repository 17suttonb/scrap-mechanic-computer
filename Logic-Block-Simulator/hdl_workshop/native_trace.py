"""Compare captured controller samples at explicit native update boundaries.

The caller supplies capture provenance and relative tick zero. No fitted
offset, tick-rate multiplier or silent signal alignment is applied.
"""
import argparse
import hashlib
import json
from pathlib import Path

from native_simulator import NativeLogicSimulator
from native_sim_cli import resolve


def compare(folder,capture):
    folder=Path(folder);raw=(folder/'blueprint.json').read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if capture.get('blueprint_sha256') and capture['blueprint_sha256']!=digest:
        raise ValueError('Trace and blueprint hashes differ')
    sim=NativeLogicSimulator(json.loads(raw))
    if capture.get('snapshot'):sim.restore(capture['snapshot'])
    for row in capture.get('inputs',[]):
        tick=row['commit_tick']
        for name,value in row['signals'].items():
            if isinstance(value,str):value=int(value,0)
            ids=resolve(folder,name)
            if not isinstance(value,int) or not 0<=value<1<<len(ids):raise ValueError('Invalid captured input value')
            for bit,identifier in enumerate(ids):
                if tick==sim.time:sim.force_input(identifier,bool(value&(1<<bit)))
                else:sim.request_input(identifier,bool(value&(1<<bit)),at_tick=tick)
    mismatches=[];checked=0;last_tick=sim.time
    for row in capture['samples']:
        tick=row['tick']
        if type(tick) is not int or tick<last_tick:raise ValueError('Captured samples must be sorted native-update ticks')
        sim.advance(tick-sim.time);last_tick=tick
        for name,expected in row['signals'].items():
            if isinstance(expected,str):expected=int(expected,0)
            actual=sim.read_ids(resolve(folder,name));checked+=1
            if actual!=expected:mismatches.append(dict(tick=tick,signal=name,expected=hex(expected),actual=hex(actual)))
    return dict(passed=not mismatches,checked_signal_samples=checked,mismatch_count=len(mismatches),
        mismatches=mismatches,blueprint_sha256=digest,model=sim.describe(),
        source=capture.get('source','Caller-supplied reference; provenance not verified'),
        alignment='Explicit relative native ticks; no automatic retiming or fitted scale')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('blueprint',type=Path);parser.add_argument('capture',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=compare(args.blueprint,json.loads(args.capture.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('mismatches','model')},indent=2))
