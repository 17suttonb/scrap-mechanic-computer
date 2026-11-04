import json
import random
import unittest
import tempfile
from pathlib import Path

from native_simulator import NativeLogicSimulator,TimerState,BUFFER_BITS,legacy_timer_fields
from native_reference import NativeReferenceSimulator
from native_timer_reference import TimerOracle
from circuit_core import GATE_UUID,SWITCH_UUID,BUTTON_UUID,MODES
from blueprint_supports import TIMER_UUID


def board(specs):
    parts=[]
    for row in specs:
        identifier,kind,targets=row[:3];active=row[3] if len(row)>3 else False
        c=dict(id=identifier,active=active,controllers=[dict(id=t) for t in targets] or None)
        shape=SWITCH_UUID if kind=='INPUT' else BUTTON_UUID if kind=='BUTTON' else TIMER_UUID if kind=='TIMER' else GATE_UUID
        if kind=='TIMER':
            delay=row[4] if len(row)>4 else 8;c.update(seconds=delay//40,ticks=delay%40)
        elif shape==GATE_UUID:c['mode']=MODES[kind]
        parts.append(dict(shapeId=shape,controller=c))
    return dict(version=4,bodies=[dict(childs=parts)])


class NativeSimulatorTests(unittest.TestCase):
    def test_clock_period_against_literal_full_tick_reference(self):
        from intel4004.clock import native_clock_period_ticks
        for settings in ((8,8,8,8),(8,8,8,6),(8,4,8,4),(1,2,3,4),(10,10,10,10)):
            # Ring transitions, rather than the helper's arithmetic, supply
            # the expected observed spacing. CAP decodes both ring edges.
            bp=board([(1,'NOR',[2]),(2,'TIMER',[3],False,settings[0]),
                      (3,'TIMER',[4],False,settings[1]),(4,'TIMER',[5],False,settings[2]),
                      (5,'TIMER',[1],False,settings[3])])
            sim=NativeReferenceSimulator(bp);edges=[];previous=False
            for _ in range(400):
                sim.step()
                if sim.values[1]!=previous:edges.append(sim.time)
                previous=sim.values[1]
            self.assertGreater(len(edges),4)
            self.assertEqual(set(b-a for a,b in zip(edges,edges[1:])),{native_clock_period_ticks(settings)})

    def assert_models_equal(self,fast,slow):
        self.assertEqual(fast.time,slow.time)
        self.assertEqual(fast.values,slow.values)
        for i in slow.timers:
            self.assertEqual(fast.timer_history(i),slow.timer_history(i),(i,fast.time))
            self.assertEqual(fast.timer_states[i].future,slow.timers[i].future)

    def test_all_modes_and_empty_input(self):
        for kind in ('AND','OR','XOR','NAND','NOR','XNOR'):
            for value in range(8):
                bp=board([(1,'INPUT',[4],bool(value&1)),(2,'INPUT',[4],bool(value&2)),
                          (3,'INPUT',[4],bool(value&4)),(4,kind,[]),(5,kind,[],True)])
                sim=NativeLogicSimulator(bp);sim.advance(1);ones=value.bit_count()
                expected=dict(AND=ones==3,OR=ones>0,XOR=ones%2==1,NAND=ones<3,NOR=ones==0,XNOR=ones%2==0)[kind]
                self.assertEqual(sim.values[4],expected);self.assertFalse(sim.values[5])

    def test_committed_stimulus_vs_controller_request(self):
        bp=board([(1,'INPUT',[2]),(2,'OR',[3]),(3,'OR',[])])
        sim=NativeLogicSimulator(bp);sim.advance(4);sim.set(1,1)
        sim.advance(1);self.assertEqual([sim.values[i] for i in (1,2,3)],[True,False,False])
        sim.advance(1);self.assertEqual([sim.values[i] for i in (1,2,3)],[True,True,False])
        sim.advance(1);self.assertTrue(sim.values[3])
        sim.force_input(1,False);sim.advance(1);self.assertFalse(sim.values[2]);self.assertTrue(sim.values[3])

    def test_saved_logic_and_lever_state_button_local_load(self):
        sim=NativeLogicSimulator(board([(1,'INPUT',[],True),(2,'BUTTON',[],True),(3,'OR',[],True)]))
        self.assertTrue(sim.values[1]);self.assertFalse(sim.values[2]);self.assertTrue(sim.values[3])
        sim.advance(1);self.assertFalse(sim.values[3])

    def test_initial_active_timer_is_not_stuck_high(self):
        for delay in (1,8,39):
            sim=NativeLogicSimulator(board([(1,'TIMER',[1],True,delay)]))
            sim.advance(1);self.assertFalse(sim.values[1])
            sim.advance(delay-1);self.assertFalse(sim.values[1])
            sim.advance(1);self.assertTrue(sim.values[1])
            sim.advance(1);self.assertFalse(sim.values[1])

    def test_disconnected_timer_drains_all_history_including_above_delay(self):
        bp=board([(1,'TIMER',[],True,8)])
        sim=NativeLogicSimulator(bp,timer_history={1:1<<2000})
        sim.advance(1);self.assertFalse(sim.values[1])
        sim.advance(1999);self.assertFalse(sim.values[1]);sim.advance(1);self.assertTrue(sim.values[1])
        sim.advance(1);self.assertFalse(sim.values[1]);self.assertEqual(int.from_bytes(sim.timer_history(1),'little'),0)

    def test_sampled_single_update_pulse_and_delay_extremes(self):
        for n in (0,1,8,39,63,64,2399,2431):
            sim=NativeLogicSimulator(board([(1,'INPUT',[2]),(2,'TIMER',[],False,n)]),input_mode='committed')
            sim.force_input(1,True);sim.advance(1);sim.force_input(1,False)
            sim.advance(n);self.assertTrue(sim.values[2]);sim.advance(1);self.assertFalse(sim.values[2])

    def test_timer_or_insertion_does_not_clear_existing_sample(self):
        sim=NativeLogicSimulator(board([(1,'INPUT',[2]),(2,'TIMER',[],False,8)]),timer_history={2:1<<8})
        sim.advance(9);self.assertTrue(sim.values[2]);sim.advance(1);self.assertFalse(sim.values[2])

    def test_native_settings_flush_raw_change_but_same_fields_preserve(self):
        bp=board([(1,'INPUT',[2],True),(2,'TIMER',[],True,40)])
        sim=NativeLogicSimulator(bp);sim.advance(20);before=sim.timer_history(2)
        self.assertFalse(sim.apply_timer_settings(2,1,0));self.assertEqual(sim.timer_history(2),before)
        future=sim.timer_states[2].future
        self.assertTrue(sim.apply_timer_settings(2,0,40));self.assertFalse(sim.values[2])
        self.assertEqual(sim.timer_history(2),bytes(304));self.assertEqual(sim.timer_states[2].future,future)
        sim.advance(40);self.assertFalse(sim.values[2]);sim.advance(1);self.assertTrue(sim.values[2])

    def test_controller_reset_is_explicit_and_electrical_low_is_not_reset(self):
        sim=NativeLogicSimulator(board([(1,'INPUT',[2]),(2,'TIMER',[],False,8)]),input_mode='committed')
        sim.set(1,True);sim.advance(1);sim.set(1,False)
        self.assertNotEqual(sim.timer_history(2),bytes(304));self.assertTrue(sim.reset_timer(2))
        sim.advance(20);self.assertFalse(sim.values[2])

    def test_safe_raw_bounds_legacy_version_is_explicit(self):
        self.assertEqual(legacy_timer_fields(0,8,None),(0,8));self.assertEqual(legacy_timer_fields(0,8,2),(0,9))
        self.assertEqual(legacy_timer_fields(0,40,2),(1,1));self.assertEqual(legacy_timer_fields(59,40,2),(59,40))
        with self.assertRaises(ValueError):NativeLogicSimulator(board([(1,'TIMER',[],False,2432)]))

    def test_graph_admission_and_first_parent_raw_timer(self):
        with self.assertRaises(ValueError):NativeLogicSimulator(board([(1,'OR',[2]),(2,'OR',[1])]))
        with self.assertRaises(ValueError):NativeLogicSimulator(board([(1,'INPUT',[2,2]),(2,'XOR',[])]))
        with self.assertRaises(ValueError):NativeLogicSimulator(board([(1,'INPUT',[3]),(2,'INPUT',[3]),(3,'TIMER',[])]))
        sim=NativeLogicSimulator(board([(1,'INPUT',[3],False),(2,'INPUT',[3],True),(3,'TIMER',[],False,0)]),connection_policy='raw')
        sim.advance(2);self.assertFalse(sim.values[3])

    def test_bulk_timer_against_literal_bytewise_oracle(self):
        rng=random.Random(773)
        for case in range(70):
            n=rng.choice((0,1,7,8,63,64,2399,2431));held=bool(rng.randrange(2));bits=rng.getrandbits(BUFFER_BITS)
            count=rng.randrange(160) if case<65 else 2600
            optimized=TimerState(n//40,n%40,bits=bits,held=held)
            literal=TimerOracle(n//40,n%40,history=bits.to_bytes(304,'little'))
            for _ in range(count):literal.step([held])
            optimized.advance_to(count)
            self.assertEqual(optimized.bits,int.from_bytes(literal.history,'little'))
            self.assertEqual(optimized.future,literal.future)

    def test_random_graphs_against_independent_full_tick_oracle(self):
        rng=random.Random(9101)
        for case in range(80):
            kinds=['INPUT','INPUT']+[rng.choice(tuple(MODES)) for _ in range(12)]+['TIMER','TIMER']
            outgoing={i:[] for i in range(1,17)};incoming={i:[] for i in range(1,17)}
            for _ in range(35):
                a=rng.randrange(1,17);b=rng.randrange(3,17)
                if b in outgoing[a] or (a!=b and a in outgoing[b]):continue
                if kinds[b-1]=='TIMER' and incoming[b]:continue
                outgoing[a].append(b);incoming[b].append(a)
            rows=[(i,kinds[i-1],outgoing[i],bool(rng.randrange(2)),rng.choice((0,1,8,39))) for i in range(1,17)]
            rng.shuffle(rows);bp=board(rows)
            history={i:rng.getrandbits(100) for i in (15,16)}
            fast=NativeLogicSimulator(bp,timer_history=history);slow=NativeReferenceSimulator(bp,timer_history=history)
            for tick in range(100):
                if tick%7==0:
                    i=rng.choice((1,2));v=bool(rng.randrange(2));fast.set(i,v);slow.set(i,v)
                if tick in (23,57):
                    i=rng.choice((15,16));n=rng.randrange(50)
                    fast.apply_timer_settings(i,n//40,n%40);slow.apply_timer_settings(i,n//40,n%40)
                if tick==78:fast.reset_timer(15);slow.reset_timer(15)
                if tick==80:
                    mode=rng.randrange(6);fast.apply_gate_mode(3,mode);slow.apply_gate_mode(3,mode)
                fast.advance(1);slow.step();self.assert_models_equal(fast,slow)

    def test_snapshot_round_trip_trace_and_large_idle_skip(self):
        bp=board([(1,'INPUT',[2]),(2,'TIMER',[3],False,8),(3,'XOR',[4]),(4,'NOR',[])])
        sim=NativeLogicSimulator(bp);sim.watch('out',[4]);sim.set(1,True);sim.advance(4)
        sim.request_input(1,False,at_tick=11)
        restored=NativeLogicSimulator(bp);restored.restore(json.loads(json.dumps(sim.snapshot())))
        for n in (1,7,20,100000):
            sim.advance(n);restored.advance(n);self.assertEqual(sim.values,restored.values)
            self.assertEqual(sim.timer_history(2),restored.timer_history(2))
        self.assertTrue(sim.trace);self.assertLess(sim.stats['timer_evaluations'],10)

    def test_snapshot_rejects_different_topology_with_same_ids(self):
        first=NativeLogicSimulator(board([(1,'INPUT',[2]),(2,'OR',[])]))
        second=NativeLogicSimulator(board([(1,'INPUT',[]),(2,'OR',[])]))
        with self.assertRaises(ValueError):second.restore(first.snapshot())

    def test_cli_stimulus_trace_and_snapshot(self):
        from native_sim_cli import run
        bp=board([(1,'INPUT',[2]),(2,'OR',[3]),(3,'TIMER',[4],False,1),(4,'OR',[])])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'blueprint.json').write_text(json.dumps(bp))
            ports=dict(reset=dict(bits=[dict(controllerId=1)]),out=dict(bits=[dict(controllerId=4)]))
            (folder/'port-map.json').write_text(json.dumps(ports))
            sim,result=run(folder,ticks=10,stimuli=[dict(tick=0,set=dict(reset=1)),dict(tick=3,set=dict(reset=0))],watches=['out'])
            self.assertEqual([(r['tick'],r['value']) for r in result['trace']],[(0,'0x0'),(5,'0x1'),(8,'0x0')])
            clone,_=run(folder,ticks=5,initial_snapshot=sim.snapshot(),watches=['out'])
            self.assertEqual(clone.time,15);self.assertFalse(clone.values[4])

    def test_reference_trace_uses_exact_ticks_and_reports_disagreement(self):
        from native_trace import compare
        bp=board([(1,'INPUT',[2]),(2,'OR',[])])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'blueprint.json').write_text(json.dumps(bp))
            (folder/'port-map.json').write_text(json.dumps(dict(out=dict(bits=[dict(controllerId=2)]))))
            capture=dict(source='Hand-derived unit fixture; not a native capture',
                inputs=[dict(commit_tick=2,signals={'#1':1})],samples=[dict(tick=2,signals=dict(out=0)),dict(tick=3,signals=dict(out=1))])
            self.assertTrue(compare(folder,capture)['passed'])
            capture['samples'][0]['signals']['out']=1
            result=compare(folder,capture)
            self.assertEqual(result['mismatch_count'],1);self.assertEqual(result['mismatches'][0]['tick'],2)


if __name__=='__main__':unittest.main(verbosity=2)
