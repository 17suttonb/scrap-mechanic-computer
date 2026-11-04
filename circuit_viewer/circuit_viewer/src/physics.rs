//! Progressive, topology-aware 2D/3D layout. No source/model data is mutated.
use std::collections::VecDeque;

pub const ITERATIONS: usize = 720;
const SPACING: f32 = 180.;
const NONE: usize = usize::MAX;
const WAVE_INTERVAL: usize = 24;
pub type Point = [f32; 3];

#[derive(Clone, Debug)]
pub struct Scene {
    pub positions: Vec<Point>,
    pub active: Vec<bool>,
    pub active_count: usize,
    pub wave: usize,
    pub waves: usize,
    pub dimensions: usize,
}

pub struct WaveLayout {
    scene: Scene,
    edges: Vec<(usize, usize)>,
    degree: Vec<usize>,
    order: Vec<usize>,
    parents: Vec<Option<usize>>,
    velocities: Vec<Point>,
    forces: Vec<Point>,
    batch: usize,
}

impl WaveLayout {
    pub fn new(
        count: usize,
        edges: Vec<(usize, usize)>,
        dimensions: usize,
        preferred_roots: &[bool],
        waves: bool,
    ) -> Self {
        assert!(dimensions == 2 || dimensions == 3);
        let mut neighbors = vec![Vec::new(); count];
        for &(a, b) in &edges {
            neighbors[a].push(b);
            neighbors[b].push(a);
        }
        let degree: Vec<_> = neighbors.iter().map(Vec::len).collect();
        // Select a root in each connected component, preferring circuit inputs.
        // Largest components lead; IDs break ties for reproducible restarts.
        let mut visited = vec![false; count];
        let mut components = Vec::new();
        for start in 0..count {
            if visited[start] {
                continue;
            }
            let mut pending = vec![start];
            visited[start] = true;
            let mut root = start;
            let mut size = 0;
            while let Some(i) = pending.pop() {
                size += 1;
                let score = |i: usize| {
                    (
                        preferred_roots.get(i).copied().unwrap_or(false),
                        degree[i],
                        std::cmp::Reverse(i),
                    )
                };
                if score(i) > score(root) {
                    root = i;
                }
                for &j in &neighbors[i] {
                    if !visited[j] {
                        visited[j] = true;
                        pending.push(j);
                    }
                }
            }
            components.push((root, size));
        }
        components.sort_by_key(|&(root, size)| (std::cmp::Reverse(size), root));
        let mut positions = vec![[0.; 3]; count];
        let mut parents = vec![None; count];
        visited.fill(false);
        let mut queue = VecDeque::new();
        for (k, &(root, _)) in components.iter().enumerate() {
            let direction = direction(k, dimensions);
            let radius = if components.len() == 1 {
                0.
            } else {
                600. * (k as f32).sqrt()
            };
            positions[root] = direction.map(|v| v * radius);
            visited[root] = true;
            queue.push_back(root);
        }
        let mut order = Vec::with_capacity(count);
        while let Some(i) = queue.pop_front() {
            order.push(i);
            for &j in &neighbors[i] {
                if !visited[j] {
                    visited[j] = true;
                    parents[j] = Some(i);
                    queue.push_back(j);
                }
            }
        }
        let target_waves = count.div_ceil(2).clamp(1, 24);
        let batch = count.div_ceil(target_waves).max(1);
        let scene = Scene {
            positions,
            active: vec![false; count],
            active_count: 0,
            wave: 0,
            waves: count.div_ceil(batch),
            dimensions,
        };
        let mut layout = Self {
            scene,
            edges,
            degree,
            order,
            parents,
            velocities: vec![[0.; 3]; count],
            forces: vec![[0.; 3]; count],
            batch,
        };
        if waves {
            layout.admit(batch);
        } else {
            layout.reveal_all();
        }
        layout
    }

    pub fn resume(scene: Scene, edges: Vec<(usize, usize)>) -> Self {
        let mut result = Self::new(scene.positions.len(), edges, scene.dimensions, &[], true);
        result.order.sort_by_key(|&i| !scene.active[i]);
        result.scene = scene;
        // A frozen partial layout resumes from the displayed positions. Pending
        // nodes are placed near their parents before normal relaxation resumes.
        result.reveal_all();
        result
    }

    fn admit(&mut self, amount: usize) {
        let end = (self.scene.active_count + amount).min(self.order.len());
        for &i in &self.order[self.scene.active_count..end] {
            if let Some(parent) = self.parents[i] {
                let offset = direction(i, self.scene.dimensions);
                for (axis, &v) in offset.iter().enumerate() {
                    self.scene.positions[i][axis] = self.scene.positions[parent][axis]
                        + v * SPACING * (0.85 + (i % 13) as f32 * 0.04);
                }
            }
            self.scene.active[i] = true;
        }
        self.scene.active_count = end;
        self.scene.wave = end.div_ceil(self.batch);
    }

    pub fn reveal_all(&mut self) {
        self.admit(self.order.len());
    }
    pub fn view(&self) -> Scene {
        self.scene.clone()
    }

    pub fn step(&mut self, iteration: usize) {
        let desired = ((iteration / WAVE_INTERVAL + 1) * self.batch).min(self.order.len());
        if desired > self.scene.active_count {
            self.admit(desired - self.scene.active_count);
        }
        if self.scene.active_count < 2 {
            return;
        }
        let tree = Tree::new(
            &self.scene.positions,
            &self.scene.active,
            self.scene.dimensions,
        );
        for i in 0..self.scene.positions.len() {
            if !self.scene.active[i] {
                continue;
            }
            let repulsion = tree.force(i, &self.scene.positions);
            for (axis, &f) in repulsion.iter().enumerate() {
                self.forces[i][axis] = f * 0.15 - self.scene.positions[i][axis] * 0.006;
            }
        }
        for &(a, b) in &self.edges {
            if !self.scene.active[a] || !self.scene.active[b] {
                continue;
            }
            let delta = sub(self.scene.positions[b], self.scene.positions[a]);
            // Soften hubs without allowing their many springs to collapse a
            // whole neighborhood into one point.
            let degree = ((self.degree[a].max(1) * self.degree[b].max(1)) as f32)
                .sqrt()
                .sqrt();
            let strength = length(delta) / SPACING * 0.15 / degree;
            for (axis, &d) in delta.iter().enumerate() {
                self.forces[a][axis] += d * strength;
                self.forces[b][axis] -= d * strength;
            }
        }
        let progress = (iteration as f32 / ITERATIONS as f32).min(1.);
        let cap = 38. * (1. - progress).powf(1.5) + 0.15;
        let mut mean = [0.; 3];
        for i in 0..self.scene.positions.len() {
            if !self.scene.active[i] {
                continue;
            }
            for axis in 0..3 {
                self.velocities[i][axis] =
                    self.velocities[i][axis] * 0.68 + self.forces[i][axis] * 0.075;
            }
            let speed = length(self.velocities[i]);
            let scale = (cap / speed.max(0.001)).min(1.);
            for (axis, m) in mean.iter_mut().enumerate() {
                self.velocities[i][axis] *= scale;
                self.scene.positions[i][axis] += self.velocities[i][axis];
                *m += self.scene.positions[i][axis];
            }
        }
        for v in &mut mean {
            *v /= self.scene.active_count as f32;
        }
        // Translate pending roots too, preserving one coordinate frame for all waves.
        for p in &mut self.scene.positions {
            for axis in 0..3 {
                p[axis] -= mean[axis];
            }
        }
    }
}

fn direction(i: usize, dimensions: usize) -> Point {
    let angle = i as f32 * 2.399_963_1;
    if dimensions == 2 {
        return [angle.cos(), angle.sin(), 0.];
    }
    let z = ((i as f32 * 0.754_877_7 + 0.37).fract() * 1.8) - 0.9;
    let r = (1. - z * z).sqrt();
    [r * angle.cos(), r * angle.sin(), z]
}
pub fn sub(a: Point, b: Point) -> Point {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
pub fn dot(a: Point, b: Point) -> f32 {
    a.iter().zip(b).map(|(a, b)| a * b).sum()
}
pub fn length(p: Point) -> f32 {
    dot(p, p).sqrt()
}

struct Cell {
    center: Point,
    half: f32,
    mean: Point,
    start: usize,
    end: usize,
    children: [usize; 8],
}
struct Tree {
    cells: Vec<Cell>,
    order: Vec<usize>,
    dimensions: usize,
    opening_squared: f32,
}
impl Tree {
    fn new(points: &[Point], active: &[bool], dimensions: usize) -> Self {
        let order: Vec<_> = active
            .iter()
            .enumerate()
            .filter(|&(_, on)| *on)
            .map(|(i, _)| i)
            .collect();
        let mut low = [f32::INFINITY; 3];
        let mut high = [f32::NEG_INFINITY; 3];
        for &i in &order {
            for axis in 0..3 {
                low[axis] = low[axis].min(points[i][axis]);
                high[axis] = high[axis].max(points[i][axis]);
            }
        }
        let center = std::array::from_fn(|a| (low[a] + high[a]) * 0.5);
        let half = (0..dimensions)
            .map(|a| (high[a] - low[a]) * 0.5)
            .fold(1., f32::max)
            + 1.;
        // Increase approximation gradually as waves grow the graph. Small
        // neighborhoods retain accuracy; large scenes spend less time on
        // distant octants while exact short-range repulsion is preserved.
        let opening = 0.5 + 0.35 * (order.len() as f32 / 20_000.).min(1.);
        let mut tree = Self {
            cells: Vec::with_capacity(order.len()),
            order,
            dimensions,
            opening_squared: opening * opening,
        };
        tree.build(0, tree.order.len(), center, half, points, 0);
        tree
    }

    fn build(
        &mut self,
        start: usize,
        end: usize,
        center: Point,
        half: f32,
        points: &[Point],
        depth: usize,
    ) -> usize {
        let mut mean = [0.; 3];
        for &i in &self.order[start..end] {
            for (axis, value) in mean.iter_mut().enumerate() {
                *value += points[i][axis];
            }
        }
        for v in &mut mean {
            *v /= (end - start).max(1) as f32;
        }
        let index = self.cells.len();
        self.cells.push(Cell {
            center,
            half,
            mean,
            start,
            end,
            children: [NONE; 8],
        });
        if end - start > 8 && depth < 24 && half > 0.01 {
            // In-place coordinate partitions form four/eight contiguous buckets.
            let mut ranges = vec![(start, end, 0)];
            for (axis, &split_at) in center.iter().enumerate().take(self.dimensions) {
                let mut split = Vec::with_capacity(ranges.len() * 2);
                for (a, b, bits) in ranges {
                    let mut middle = a;
                    for k in a..b {
                        if points[self.order[k]][axis] < split_at {
                            self.order.swap(k, middle);
                            middle += 1;
                        }
                    }
                    split.extend([(a, middle, bits), (middle, b, bits | (1 << axis))]);
                }
                ranges = split;
            }
            for (a, b, bits) in ranges {
                if a == b {
                    continue;
                }
                let child_center = std::array::from_fn(|axis| {
                    if axis < self.dimensions {
                        center[axis]
                            + if bits & (1 << axis) == 0 {
                                -half * 0.5
                            } else {
                                half * 0.5
                            }
                    } else {
                        0.
                    }
                });
                let child = self.build(a, b, child_center, half * 0.5, points, depth + 1);
                self.cells[index].children[bits] = child;
            }
        }
        index
    }

    fn force(&self, i: usize, points: &[Point]) -> Point {
        self.visit(0, i, points)
    }
    fn visit(&self, index: usize, i: usize, points: &[Point]) -> Point {
        let c = &self.cells[index];
        let delta = sub(points[i], c.mean);
        let d2 = dot(delta, delta);
        let contains = (0..self.dimensions).all(|a| (points[i][a] - c.center[a]).abs() <= c.half);
        if !contains && c.half * c.half * 4. < d2 * self.opening_squared {
            return delta.map(|d| d * SPACING * SPACING * (c.end - c.start) as f32 / d2.max(36.));
        }
        let mut force = [0.; 3];
        if c.children.iter().all(|&n| n == NONE) {
            for &j in &self.order[c.start..c.end] {
                if i == j {
                    continue;
                }
                let mut d = sub(points[i], points[j]);
                let mut d2 = dot(d, d);
                if d2 < 0.001 {
                    d = direction(
                        i.min(j).wrapping_mul(31).wrapping_add(i.max(j)),
                        self.dimensions,
                    )
                    .map(|v| v * if i < j { 1. } else { -1. });
                    d2 = 1.;
                }
                for axis in 0..3 {
                    force[axis] += d[axis] * SPACING * SPACING / d2.max(36.);
                }
            }
        } else {
            for &child in &c.children {
                if child != NONE {
                    let f = self.visit(child, i, points);
                    for axis in 0..3 {
                        force[axis] += f[axis];
                    }
                }
            }
        }
        force
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn waves_expand_through_topology_and_admit_every_node() {
        let edges = (0..99).map(|i| (i, i + 1)).collect();
        let mut l = WaveLayout::new(100, edges, 3, &[true], true);
        assert!(l.scene.active_count < 100);
        assert!(l.scene.active[0]);
        for iteration in 0..ITERATIONS {
            l.step(iteration);
            for i in 1..100 {
                if l.scene.active[i] {
                    assert!(l.scene.active[i - 1]);
                }
            }
        }
        assert_eq!(l.scene.active_count, 100);
        assert_eq!(l.scene.wave, l.scene.waves);
        assert!(l.scene.positions.iter().flatten().all(|v| v.is_finite()));
    }
    #[test]
    fn planar_layout_stays_planar_and_spatial_layout_uses_depth() {
        for dims in [2, 3] {
            let mut l =
                WaveLayout::new(20, (0..19).map(|i| (i, i + 1)).collect(), dims, &[], false);
            for i in 0..100 {
                l.step(i);
            }
            if dims == 2 {
                assert!(l.scene.positions.iter().all(|p| p[2] == 0.));
            } else {
                let z: Vec<_> = l.scene.positions.iter().map(|p| p[2]).collect();
                assert!(
                    z.iter().copied().fold(f32::NEG_INFINITY, f32::max)
                        - z.iter().copied().fold(f32::INFINITY, f32::min)
                        > 50.
                );
            }
        }
    }
    #[test]
    fn resume_reveals_pending_nodes_and_preserves_existing_positions() {
        let mut l = WaveLayout::new(20, (0..19).map(|i| (i, i + 1)).collect(), 3, &[], true);
        l.step(0);
        let before = l.view();
        let next = WaveLayout::resume(before.clone(), l.edges.clone()).view();
        assert_eq!(next.active_count, 20);
        for (i, &on) in before.active.iter().enumerate() {
            if on {
                assert_eq!(before.positions[i], next.positions[i]);
            }
        }
    }
    #[test]
    fn disconnected_empty_single_and_coincident_graphs_are_finite() {
        for count in [0, 1, 2, 30] {
            for dims in [2, 3] {
                let mut l = WaveLayout::new(count, vec![], dims, &[], true);
                l.reveal_all();
                l.scene.positions.fill([0.; 3]);
                for i in 0..80 {
                    l.step(i);
                }
                assert_eq!(l.scene.active_count, count);
                assert!(l.scene.positions.iter().flatten().all(|v| v.is_finite()));
                if count > 1 {
                    assert!(length(sub(l.scene.positions[0], l.scene.positions[1])) > 1.);
                }
            }
        }
    }
    #[test]
    fn octree_matches_exact_repulsion_in_both_dimensions() {
        for dims in [2, 3] {
            let points: Vec<_> = (0..300)
                .map(|i| direction(i, dims).map(|v| v * (100. + (i as f32).sqrt() * 100.)))
                .collect();
            let tree = Tree::new(&points, &vec![true; 300], dims);
            for i in [0, 13, 55, 299] {
                let mut exact = [0.; 3];
                for j in 0..points.len() {
                    if i == j {
                        continue;
                    }
                    let d = sub(points[i], points[j]);
                    let f = SPACING * SPACING / dot(d, d).max(36.);
                    for a in 0..3 {
                        exact[a] += d[a] * f;
                    }
                }
                let relative = length(sub(tree.force(i, &points), exact)) / length(exact).max(1.);
                assert!(
                    relative < 0.12,
                    "dims={dims} node={i} relative={relative}, exact={exact:?}, approx={:?}",
                    tree.force(i, &points)
                );
            }
        }
    }
    #[test]
    fn large_spatial_graph_with_hub_stays_finite() {
        let n = 20_000;
        let edges = (1..n).flat_map(|i| [(i - 1, i), (0, i)]).collect();
        let mut l = WaveLayout::new(n, edges, 3, &[], false);
        let start = std::time::Instant::now();
        for i in 0..12 {
            l.step(i);
        }
        assert_eq!(l.scene.active_count, n);
        assert!(l.scene.positions.iter().flatten().all(|v| v.is_finite()));
        eprintln!("20,000-node 3D layout, 12 steps: {:?}", start.elapsed());
    }

    #[test]
    fn connected_nodes_settle_closer_than_an_isolated_component() {
        let mut l = WaveLayout::new(3, vec![(0, 1)], 3, &[], false);
        for i in 0..ITERATIONS {
            l.step(i);
        }
        let connected = length(sub(l.scene.positions[0], l.scene.positions[1]));
        assert!(connected > 100. && connected < 500.);
        assert!(length(sub(l.scene.positions[0], l.scene.positions[2])) > connected * 1.3);
    }

    #[test]
    fn input_roots_and_disconnected_feedback_components_spawn_once() {
        let edges = vec![(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)];
        let mut l = WaveLayout::new(6, edges, 3, &[false, false, true, false, true, false], true);
        assert!(l.scene.active[2] && l.scene.active[4]);
        assert_eq!(l.scene.active_count, 2);
        for i in 0..ITERATIONS {
            l.step(i);
        }
        assert!(l.scene.active.iter().all(|on| *on));
        assert_eq!(
            l.order
                .iter()
                .copied()
                .collect::<std::collections::HashSet<_>>()
                .len(),
            6
        );
        assert!(l.scene.positions.iter().flatten().all(|v| v.is_finite()));
    }
}
