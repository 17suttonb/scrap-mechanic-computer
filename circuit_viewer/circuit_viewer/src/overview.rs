//! Bounded, static hierarchy for large circuit navigation. Clusters are inferred
//! from connectivity, not claimed to be the original HDL module hierarchy.
use crate::model::{Circuit, Kind};
use std::collections::{HashMap, HashSet, VecDeque};

pub const LARGE_CIRCUIT: usize = 15_000;
pub const LEAF_SIZE: usize = 64;
const FANOUT: usize = 16;
const ROOT_SIZE: usize = 32;
pub const NONE: usize = usize::MAX;

pub struct Cluster {
    pub name: String,
    pub sample: String,
    pub parent: Option<usize>,
    pub children: Vec<usize>,
    pub members: Vec<usize>,
    pub inputs: usize,
    pub outputs: usize,
    pub dominant: Kind,
    pub internal: usize,
    pub incoming: usize,
    pub outgoing: usize,
}

pub struct Hierarchy {
    pub clusters: Vec<Cluster>,
    pub root: usize,
    pub node_leaf: Vec<usize>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Item {
    Cluster(usize),
    Node(usize),
}
impl Item {
    pub fn size(self, h: &Hierarchy) -> usize {
        match self {
            Self::Cluster(c) => h.clusters[c].members.len(),
            Self::Node(_) => 1,
        }
    }
}

pub struct Bundle {
    pub from: usize,
    pub to: usize,
    /// Keep endpoints so preview and tracing counts remain exact after folding.
    pub endpoints: Vec<(usize, usize)>,
    pub active: usize,
    pub reciprocal: bool,
}

pub struct Page {
    pub scope: usize,
    pub items: Vec<Item>,
    pub positions: Vec<[f32; 2]>,
    pub bounds: [f32; 4],
    pub bundles: Vec<Bundle>,
    /// Dense node-to-visible-item index; NONE means outside this page.
    pub mapping: Vec<usize>,
    pub internal: usize,
    pub incoming: usize,
    pub outgoing: usize,
    pub folded: Vec<usize>,
}

impl Hierarchy {
    pub fn build(circuit: &Circuit) -> Self {
        let n = circuit.nodes.len();
        let mut adjacency = vec![Vec::new(); n];
        for &(a, b) in &circuit.edges {
            adjacency[a].push((b, 1));
            adjacency[b].push((a, 1));
        }
        let degree: Vec<_> = adjacency.iter().map(Vec::len).collect();
        // Follow small local neighborhoods before broad clock/control fanout.
        for row in &mut adjacency {
            row.sort_unstable_by_key(|&(j, _)| (degree[j], j));
        }
        let mut result = Self {
            clusters: Vec::new(),
            root: 0,
            node_leaf: vec![0; n],
        };
        let mut level = Vec::new();
        for members in partition(&adjacency, LEAF_SIZE) {
            let id = result.push_cluster(members, vec![], circuit);
            for &i in &result.clusters[id].members {
                result.node_leaf[i] = id;
            }
            level.push(id);
        }
        // Even an empty circuit has a navigable, finite root page.
        if level.is_empty() {
            level.push(result.push_cluster(vec![], vec![], circuit));
        }
        while level.len() > ROOT_SIZE {
            let mut mapping = vec![0; n];
            for (slot, &id) in level.iter().enumerate() {
                for &i in &result.clusters[id].members {
                    mapping[i] = slot;
                }
            }
            let weights = aggregate(&circuit.edges, &mapping);
            let mut adjacency = vec![Vec::new(); level.len()];
            for ((a, b), count) in weights {
                if a != b {
                    adjacency[a].push((b, count));
                    adjacency[b].push((a, count));
                }
            }
            for row in &mut adjacency {
                row.sort_unstable_by_key(|&(j, w)| (std::cmp::Reverse(w), j));
            }
            let mut next = Vec::new();
            for group in partition(&adjacency, FANOUT) {
                let children: Vec<_> = group.into_iter().map(|i| level[i]).collect();
                let members = children
                    .iter()
                    .flat_map(|&c| result.clusters[c].members.iter().copied())
                    .collect();
                next.push(result.push_cluster(members, children, circuit));
            }
            level = next;
        }
        if level.len() == 1 {
            result.root = level[0];
        } else {
            result.root = result.push_cluster((0..n).collect(), level, circuit);
        }
        result.clusters[result.root].name = "Whole circuit".into();
        let paths: Vec<_> = (0..n).map(|i| result.path_to(i)).collect();
        for &(a, b) in &circuit.edges {
            for &c in &paths[a] {
                if paths[b].contains(&c) {
                    result.clusters[c].internal += 1;
                } else {
                    result.clusters[c].outgoing += 1;
                }
            }
            for &c in &paths[b] {
                if !paths[a].contains(&c) {
                    result.clusters[c].incoming += 1;
                }
            }
        }
        result
    }

    fn push_cluster(
        &mut self,
        members: Vec<usize>,
        children: Vec<usize>,
        circuit: &Circuit,
    ) -> usize {
        let id = self.clusters.len();
        let mut counts = [0; 8];
        let mut sample = String::new();
        for &i in &members {
            let node = &circuit.nodes[i];
            counts[kind_index(node.kind)] += 1;
            let label = node.label.trim();
            let remainder = label.strip_prefix(node.kind.name()).unwrap_or(label).trim();
            let generic = label == node.kind.name()
                || (label.starts_with(node.kind.name())
                    && remainder
                        .chars()
                        .all(|c| c.is_ascii_digit() || matches!(c, '[' | ']' | ' ')));
            if sample.is_empty() && !generic {
                sample = label.into();
            }
        }
        if sample.is_empty() {
            sample = "Connected logic".into();
        }
        let kinds = [
            Kind::Input,
            Kind::Output,
            Kind::And,
            Kind::Or,
            Kind::Xor,
            Kind::Nand,
            Kind::Nor,
            Kind::Xnor,
        ];
        let dominant = kinds[(0..8).max_by_key(|&i| counts[i]).unwrap()];
        for &child in &children {
            self.clusters[child].parent = Some(id);
        }
        self.clusters.push(Cluster {
            name: format!("Cluster {}", id + 1),
            sample,
            parent: None,
            children,
            members,
            inputs: counts[0],
            outputs: counts[1],
            dominant,
            internal: 0,
            incoming: 0,
            outgoing: 0,
        });
        id
    }

    pub fn path_to(&self, node: usize) -> Vec<usize> {
        let mut path = vec![self.node_leaf[node]];
        while let Some(parent) = self.clusters[*path.last().unwrap()].parent {
            path.push(parent);
        }
        path.reverse();
        path
    }

    pub fn page(&self, scope: usize, circuit: &Circuit) -> Page {
        let cluster = &self.clusters[scope];
        let items: Vec<_> = if cluster.children.is_empty() {
            cluster.members.iter().copied().map(Item::Node).collect()
        } else {
            cluster
                .children
                .iter()
                .copied()
                .map(Item::Cluster)
                .collect()
        };
        let mut mapping = vec![NONE; circuit.nodes.len()];
        for (slot, item) in items.iter().enumerate() {
            match *item {
                Item::Node(i) => mapping[i] = slot,
                Item::Cluster(c) => {
                    for &i in &self.clusters[c].members {
                        mapping[i] = slot;
                    }
                }
            }
        }
        let mut connections: HashMap<(usize, usize), Vec<(usize, usize)>> = HashMap::new();
        let mut folded = vec![0; items.len()];
        let (mut internal, mut incoming, mut outgoing) = (0, 0, 0);
        for &(a, b) in &circuit.edges {
            let (i, j) = (mapping[a], mapping[b]);
            match (i != NONE, j != NONE) {
                (true, true) => {
                    internal += 1;
                    if i == j {
                        folded[i] += 1;
                    } else {
                        connections.entry((i, j)).or_default().push((a, b));
                    }
                }
                (true, false) => outgoing += 1,
                (false, true) => incoming += 1,
                _ => {}
            }
        }
        let mut bundles: Vec<_> = connections
            .into_iter()
            .map(|((from, to), endpoints)| Bundle {
                from,
                to,
                endpoints,
                active: 0,
                reciprocal: false,
            })
            .collect();
        bundles.sort_unstable_by_key(|b| (std::cmp::Reverse(b.endpoints.len()), b.from, b.to));
        let pairs: HashSet<_> = bundles.iter().map(|b| (b.from, b.to)).collect();
        for b in &mut bundles {
            b.reciprocal = pairs.contains(&(b.to, b.from));
        }
        let columns = ((items.len() as f32 * 0.65).sqrt().ceil() as usize).max(1);
        let positions: Vec<_> = (0..items.len())
            .map(|i| [(i % columns) as f32 * 270., (i / columns) as f32 * 155.])
            .collect();
        let rows = items.len().div_ceil(columns);
        let bounds = if items.is_empty() {
            [0., 0., 400., 300.]
        } else {
            [
                0.,
                0.,
                (columns - 1) as f32 * 270. + 220.,
                (rows - 1) as f32 * 155. + 112.,
            ]
        };
        let mut page = Page {
            scope,
            items,
            positions,
            bounds,
            bundles,
            mapping,
            internal,
            incoming,
            outgoing,
            folded,
        };
        page.update_signals(&circuit.states);
        page
    }

    pub fn active_counts(&self, states: &[bool]) -> Vec<usize> {
        let mut counts = vec![0; self.clusters.len()];
        for (node, &on) in states.iter().enumerate() {
            if !on {
                continue;
            }
            let mut current = Some(self.node_leaf[node]);
            while let Some(c) = current {
                counts[c] += 1;
                current = self.clusters[c].parent;
            }
        }
        counts
    }
}

impl Page {
    pub fn update_signals(&mut self, states: &[bool]) {
        for bundle in &mut self.bundles {
            bundle.active = bundle.endpoints.iter().filter(|&&(a, _)| states[a]).count();
        }
    }

    pub fn trace_items(&self, start: usize, upstream: Option<bool>) -> Vec<bool> {
        let mut marked = vec![false; self.items.len()];
        marked[start] = true;
        if upstream.is_none() {
            for b in &self.bundles {
                if b.from == start {
                    marked[b.to] = true;
                }
                if b.to == start {
                    marked[b.from] = true;
                }
            }
        } else {
            let mut graph = vec![Vec::new(); self.items.len()];
            for b in &self.bundles {
                if upstream == Some(true) {
                    graph[b.to].push(b.from);
                } else {
                    graph[b.from].push(b.to);
                }
            }
            let mut pending = vec![start];
            while let Some(i) = pending.pop() {
                for &j in &graph[i] {
                    if !marked[j] {
                        marked[j] = true;
                        pending.push(j);
                    }
                }
            }
        }
        marked
    }
}

fn kind_index(kind: Kind) -> usize {
    match kind {
        Kind::Input => 0,
        Kind::Output => 1,
        Kind::And => 2,
        Kind::Or => 3,
        Kind::Xor => 4,
        Kind::Nand => 5,
        Kind::Nor => 6,
        Kind::Xnor => 7,
    }
}
fn aggregate(edges: &[(usize, usize)], mapping: &[usize]) -> HashMap<(usize, usize), usize> {
    let mut result = HashMap::new();
    for &(a, b) in edges {
        *result.entry((mapping[a], mapping[b])).or_insert(0) += 1;
    }
    result
}

fn partition(adjacency: &[Vec<(usize, usize)>], limit: usize) -> Vec<Vec<usize>> {
    let mut seeds: Vec<_> = (0..adjacency.len()).collect();
    seeds.sort_unstable_by_key(|&i| (adjacency[i].len(), i));
    let mut assigned = vec![false; seeds.len()];
    let mut queued = vec![NONE; seeds.len()];
    let mut seed = 0;
    let mut result = Vec::new();
    let mut assigned_count = 0;
    while assigned_count < seeds.len() {
        let generation = result.len();
        let mut group = Vec::new();
        let mut queue = VecDeque::new();
        while group.len() < limit && assigned_count < seeds.len() {
            if queue.is_empty() {
                while assigned[seeds[seed]] {
                    seed += 1;
                }
                queue.push_back(seeds[seed]);
            }
            let i = queue.pop_front().unwrap();
            if assigned[i] {
                continue;
            }
            assigned[i] = true;
            assigned_count += 1;
            group.push(i);
            for &(j, _) in &adjacency[i] {
                if !assigned[j] && queued[j] != generation {
                    queued[j] = generation;
                    queue.push_back(j);
                }
            }
        }
        result.push(group);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{
        fs,
        path::PathBuf,
        sync::atomic::{AtomicUsize, Ordering},
    };
    static SERIAL: AtomicUsize = AtomicUsize::new(0);
    fn circuit(n: usize, edges: Vec<(usize, usize)>) -> Circuit {
        let path: PathBuf = std::env::temp_dir().join(format!(
            "circuit-overview-{}-{}.json",
            std::process::id(),
            SERIAL.fetch_add(1, Ordering::Relaxed)
        ));
        let nodes: Vec<_> = (0..n).map(|i| json!({"id":i+1,"kind":"OR","label":format!("Gate {i}"),"x":i as f32*180.,"y":0.})).collect();
        let edges: Vec<_> = edges.iter().map(|&(a, b)| [a + 1, b + 1]).collect();
        fs::write(&path,serde_json::to_vec(&json!({"format":"scrap-circuit","version":1,"name":"Overview test","nodes":nodes,"edges":edges})).unwrap()).unwrap();
        let result = Circuit::load(&path, true).unwrap();
        fs::remove_file(path).unwrap();
        result
    }

    #[test]
    fn large_hierarchy_bounds_pages_and_preserves_every_node() {
        let n = 20_000;
        let c = circuit(n, (1..n).flat_map(|i| [(i - 1, i), (0, i)]).collect());
        let start = std::time::Instant::now();
        let h = Hierarchy::build(&c);
        assert!(h.clusters[h.root].children.len() <= ROOT_SIZE);
        let mut coverage = vec![0; n];
        for cluster in &h.clusters {
            if cluster.children.is_empty() {
                assert!(cluster.members.len() <= LEAF_SIZE);
                for &i in &cluster.members {
                    coverage[i] += 1;
                }
            }
        }
        assert!(coverage.iter().all(|&count| count == 1));
        for i in [0, 63, 1000, n - 1] {
            let path = h.path_to(i);
            assert_eq!(path[0], h.root);
            assert!(h.clusters[*path.last().unwrap()].members.contains(&i));
        }
        eprintln!(
            "20,000-node hierarchy: {} clusters in {:?}",
            h.clusters.len(),
            start.elapsed()
        );
    }

    #[test]
    fn page_bundles_account_for_all_wires_and_direction() {
        let c = circuit(
            180,
            (0..179).flat_map(|i| [(i, i + 1), (i + 1, i)]).collect(),
        );
        let h = Hierarchy::build(&c);
        let page = h.page(h.root, &c);
        assert_eq!(
            page.folded.iter().sum::<usize>()
                + page
                    .bundles
                    .iter()
                    .map(|b| b.endpoints.len())
                    .sum::<usize>(),
            c.edges.len()
        );
        assert_eq!(page.internal, c.edges.len());
        assert!(page.bundles.iter().all(|b| b.reciprocal));
        assert_eq!(page.incoming + page.outgoing, 0);
        let leaf = h.page(h.node_leaf[90], &c);
        let cluster = &h.clusters[h.node_leaf[90]];
        assert_eq!(
            (cluster.internal, cluster.incoming, cluster.outgoing),
            (leaf.internal, leaf.incoming, leaf.outgoing)
        );
        assert_eq!(
            leaf.internal + leaf.incoming + leaf.outgoing,
            c.edges
                .iter()
                .filter(|&&(a, b)| leaf.mapping[a] != NONE || leaf.mapping[b] != NONE)
                .count()
        );
        for b in &leaf.bundles {
            for &(a, d) in &b.endpoints {
                assert_eq!((leaf.mapping[a], leaf.mapping[d]), (b.from, b.to));
            }
        }
    }

    #[test]
    fn isolated_nodes_empty_circuits_and_feedback_are_deterministic() {
        for n in [0, 1, 65, 20_000] {
            let c = circuit(n, vec![]);
            let a = Hierarchy::build(&c);
            let b = Hierarchy::build(&c);
            assert_eq!(a.node_leaf, b.node_leaf);
            assert_eq!(a.clusters[a.root].members.len(), n);
            assert!(a.page(a.root, &c).bounds.iter().all(|v| v.is_finite()));
        }
        let c = circuit(3, vec![(0, 1), (1, 2), (2, 0)]);
        let h = Hierarchy::build(&c);
        let p = h.page(h.root, &c);
        assert!(p.trace_items(0, Some(true)).iter().all(|&v| v));
        assert!(p.trace_items(0, Some(false)).iter().all(|&v| v));
    }

    #[test]
    fn preview_aggregation_updates_without_rebuilding_or_writing_source() {
        let mut c = circuit(150, (0..149).map(|i| (i, i + 1)).collect());
        let h = Hierarchy::build(&c);
        let mut p = h.page(h.root, &c);
        assert_eq!(h.active_counts(&c.states)[h.root], 0);
        c.states.fill(true);
        p.update_signals(&c.states);
        assert_eq!(h.active_counts(&c.states)[h.root], 150);
        assert!(p.bundles.iter().all(|b| b.active == b.endpoints.len()));
        let selected = h.path_to(149);
        assert_eq!(selected[0], h.root);
    }
}
