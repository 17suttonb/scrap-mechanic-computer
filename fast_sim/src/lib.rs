//! The native-logic-2 controller model, compiled for speed.
//!
//! Same semantics as `Logic-Block-Simulator/hdl_workshop/native_simulator.py`, which stays the
//! authority (`fast_sim/verify.py` compares the two state by state). All gates due at a tick compute
//! from committed values, then every result publishes together. Two things make it fast:
//! a gate keeps a count of its high parents, so evaluating it is O(1), and a timer with empty
//! history is an exact delay line, so it only needs a queue of its pending output changes.
//!
//! Not modelled here: timer history loaded from outside, changing a gate or timer setting while
//! running, and snapshots. Use the Python simulator for those.
use std::collections::{BTreeMap, VecDeque};

const INPUT: u8 = 6;
const TIMER: u8 = 7;

pub struct Sim {
    time: u64,
    kind: Vec<u8>,
    value: Vec<u8>,
    parents: Vec<u32>,
    high: Vec<u32>,
    delay: Vec<u32>,
    child_start: Vec<u32>,
    child: Vec<u32>,
    due: Vec<u32>,
    next_due: Vec<u32>,
    scheduled: Vec<u64>,
    timers: Vec<u32>,
    queue: Vec<VecDeque<(u64, u8)>>,
    timer_slot: Vec<u32>,
    inputs: BTreeMap<u64, Vec<(u32, u8)>>,
    updates: Vec<(u32, u8)>,
}

#[inline]
fn evaluate(mode: u8, high: u32, parents: u32) -> u8 {
    if parents == 0 {
        return 0;
    }
    (match mode {
        0 => high == parents,
        1 => high > 0,
        2 => high & 1 == 1,
        3 => high != parents,
        4 => high == 0,
        _ => high & 1 == 0,
    }) as u8
}

impl Sim {
    fn next_event(&self) -> Option<u64> {
        let mut next = if self.due.is_empty() { u64::MAX } else { self.time + 1 };
        for q in &self.queue {
            if let Some(&(at, _)) = q.front() {
                next = next.min(at);
            }
        }
        if let Some((&at, _)) = self.inputs.first_key_value() {
            next = next.min(at);
        }
        (next != u64::MAX).then_some(next)
    }

    fn step(&mut self, at: u64) {
        let mut updates = std::mem::take(&mut self.updates);
        updates.clear();
        if self.inputs.first_key_value().is_some_and(|(&t, _)| t == at) {
            let (_, list) = self.inputs.pop_first().unwrap();
            updates.extend(list);
        }
        if at == self.time + 1 {
            for &gate in &self.due {
                let g = gate as usize;
                updates.push((gate, evaluate(self.kind[g], self.high[g], self.parents[g])));
            }
            self.due.clear();
        }
        for slot in 0..self.queue.len() {
            while self.queue[slot].front().is_some_and(|&(t, _)| t == at) {
                let (_, v) = self.queue[slot].pop_front().unwrap();
                updates.push((self.timers[slot], v));
            }
        }
        self.time = at;
        for &(node, new) in &updates {
            let n = node as usize;
            if self.value[n] == new {
                continue;
            }
            self.value[n] = new;
            for c in self.child_start[n] as usize..self.child_start[n + 1] as usize {
                let child = self.child[c] as usize;
                if self.kind[child] == TIMER {
                    let slot = self.timer_slot[child] as usize;
                    self.queue[slot].push_back((at + 1 + self.delay[child] as u64, new));
                } else {
                    if new == 1 {
                        self.high[child] += 1;
                    } else {
                        self.high[child] -= 1;
                    }
                    if self.scheduled[child] != at {
                        self.scheduled[child] = at;
                        self.next_due.push(child as u32);
                    }
                }
            }
        }
        std::mem::swap(&mut self.due, &mut self.next_due);
        self.next_due.clear();
        self.updates = updates;
    }
}

/// kinds: 0..=5 gate modes, 6 input, 7 timer. parent_start has n + 1 entries indexing parents.
///
/// # Safety
/// Every pointer must cover the lengths described above.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_new(n: usize, kinds: *const u8, values: *const u8, delays: *const u32, parent_start: *const u32, parents: *const u32) -> *mut Sim {
    let (kind, value, delay, starts) = unsafe {
        (
            std::slice::from_raw_parts(kinds, n).to_vec(),
            std::slice::from_raw_parts(values, n).to_vec(),
            std::slice::from_raw_parts(delays, n).to_vec(),
            std::slice::from_raw_parts(parent_start, n + 1).to_vec(),
        )
    };
    let edges = unsafe { std::slice::from_raw_parts(parents, starts[n] as usize) };
    let mut counts = vec![0u32; n + 1];
    let mut high = vec![0u32; n];
    let mut parent_count = vec![0u32; n];
    for t in 0..n {
        parent_count[t] = starts[t + 1] - starts[t];
        for &p in &edges[starts[t] as usize..starts[t + 1] as usize] {
            counts[p as usize + 1] += 1;
            high[t] += value[p as usize] as u32;
        }
    }
    for i in 0..n {
        counts[i + 1] += counts[i];
    }
    let child_start = counts.clone();
    let mut child = vec![0u32; edges.len()];
    for t in 0..n {
        for &p in &edges[starts[t] as usize..starts[t + 1] as usize] {
            child[counts[p as usize] as usize] = t as u32;
            counts[p as usize] += 1;
        }
    }
    let mut sim = Sim {
        time: 0,
        scheduled: vec![u64::MAX; n],
        due: (0..n as u32).filter(|&i| kind[i as usize] < INPUT).collect(),
        next_due: Vec::new(),
        timers: Vec::new(),
        queue: Vec::new(),
        timer_slot: vec![0; n],
        inputs: BTreeMap::new(),
        updates: Vec::new(),
        parents: parent_count,
        high,
        child_start,
        child,
        delay,
        kind,
        value,
    };
    for t in 0..n {
        if sim.kind[t] != TIMER {
            continue;
        }
        sim.timer_slot[t] = sim.timers.len() as u32;
        sim.timers.push(t as u32);
        let held = sim.parents[t] > 0 && sim.high[t] > 0;
        let delay = sim.delay[t] as u64;
        let mut q = VecDeque::new();
        // An active timer starts with empty history, so it drops at once unless its input already
        // reaches the output with no delay.
        if sim.value[t] == 1 && !(held && delay == 0) {
            q.push_back((1, 0));
        }
        if held {
            q.push_back((1 + delay, 1));
        }
        sim.queue.push(q);
    }
    Box::into_raw(Box::new(sim))
}

/// # Safety
/// `sim` must come from `sim_new` and not be used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_free(sim: *mut Sim) {
    drop(unsafe { Box::from_raw(sim) });
}

/// # Safety
/// `sim` must come from `sim_new`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_time(sim: *const Sim) -> u64 {
    unsafe { (*sim).time }
}

/// Request an input level at a future tick; a later request for the same input and tick replaces it.
///
/// # Safety
/// `sim` must come from `sim_new`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_request(sim: *mut Sim, node: u32, value: u8, at: u64) {
    let list = unsafe { &mut *sim }.inputs.entry(at).or_default();
    match list.iter_mut().find(|e| e.0 == node) {
        Some(entry) => entry.1 = value,
        None => list.push((node, value)),
    }
}

/// # Safety
/// `sim` must come from `sim_new`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_advance(sim: *mut Sim, updates: u64) {
    let sim = unsafe { &mut *sim };
    let end = sim.time + updates;
    while let Some(at) = sim.next_event() {
        if at > end {
            break;
        }
        sim.step(at);
    }
    sim.time = end;
}

/// Pack the values of `nodes` into little-endian bits in `out`, which holds (len + 7) / 8 bytes.
///
/// # Safety
/// `sim` must come from `sim_new`; `nodes` and `out` must cover those lengths.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_read(sim: *const Sim, nodes: *const u32, len: usize, out: *mut u8) {
    let sim = unsafe { &*sim };
    let nodes = unsafe { std::slice::from_raw_parts(nodes, len) };
    let out = unsafe { std::slice::from_raw_parts_mut(out, len.div_ceil(8)) };
    out.fill(0);
    for (i, &node) in nodes.iter().enumerate() {
        out[i / 8] |= sim.value[node as usize] << (i % 8);
    }
}

/// Copy every node's value, one byte each, in `sim_new` order.
///
/// # Safety
/// `sim` must come from `sim_new`; `out` must hold one byte per node.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sim_values(sim: *const Sim, out: *mut u8) {
    let sim = unsafe { &*sim };
    unsafe { std::slice::from_raw_parts_mut(out, sim.value.len()) }.copy_from_slice(&sim.value);
}
