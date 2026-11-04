//! File readers and parallel Boolean preview. This module never writes source files.
use serde::Deserialize;
use serde_json::Value;
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    fs,
    path::{Path, PathBuf},
};

const GATE: &str = "9f0f56e8-2c31-4d83-996c-d00a9b296c3f";
const SWITCH: &str = "7cf717d7-d167-4f2d-a6e7-6b2c70aa3986";
pub const KINDS: [&str; 8] = ["INPUT", "OUTPUT", "AND", "OR", "XOR", "NAND", "NOR", "XNOR"];

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum Kind {
    Input,
    Output,
    And,
    Or,
    Xor,
    Nand,
    Nor,
    Xnor,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Input => "INPUT",
            Self::Output => "OUTPUT",
            Self::And => "AND",
            Self::Or => "OR",
            Self::Xor => "XOR",
            Self::Nand => "NAND",
            Self::Nor => "NOR",
            Self::Xnor => "XNOR",
        }
    }
    pub fn color(self) -> [u8; 3] {
        match self {
            Self::Input => [76, 111, 227],
            Self::Output => [245, 215, 66],
            Self::And => [223, 127, 1],
            Self::Or => [44, 184, 147],
            Self::Xor => [146, 99, 217],
            Self::Nand => [217, 104, 82],
            Self::Nor => [79, 161, 207],
            Self::Xnor => [198, 105, 173],
        }
    }
    fn mode(mode: u64) -> Option<Self> {
        [
            Self::And,
            Self::Or,
            Self::Xor,
            Self::Nand,
            Self::Nor,
            Self::Xnor,
        ]
        .get(mode as usize)
        .copied()
    }
}

#[derive(Debug, Deserialize)]
pub struct Node {
    pub id: u64,
    pub kind: Kind,
    pub x: f32,
    pub y: f32,
    pub label: String,
    #[serde(default)]
    pub initial: bool,
    #[serde(skip)]
    pub controller_id: Option<u64>,
    #[serde(skip)]
    pub physical: Option<[i64; 3]>,
    #[serde(skip)]
    pub physical_only: bool,
}

#[derive(Deserialize)]
struct Project {
    format: String,
    version: u32,
    #[serde(default = "untitled")]
    name: String,
    nodes: Vec<Node>,
    edges: Vec<[u64; 2]>,
}
fn untitled() -> String {
    "Untitled circuit".into()
}

pub struct Circuit {
    pub name: String,
    pub nodes: Vec<Node>,
    /// Endpoints are dense indices, independent of source/controller IDs.
    pub edges: Vec<(usize, usize)>,
    pub incoming: Vec<Vec<usize>>,
    pub outgoing: Vec<Vec<usize>>,
    pub states: Vec<bool>,
    scratch: Vec<bool>,
    pub ticks: u64,
    pub source: PathBuf,
    pub report: String,
    pub bounds: [f32; 4],
    // Fixed-size diagram cells let painting and hit-testing visit only visible nodes.
    spatial: HashMap<(i32, i32), Vec<usize>>,
}

fn json(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_slice(bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&bytes))
        .map_err(|e| format!("{}: {e}", path.display()))
}

impl Circuit {
    pub fn load(path: &Path, prefer_project: bool) -> Result<Self, String> {
        let path = if path.is_dir() {
            path.join("blueprint.json")
        } else {
            path.to_path_buf()
        };
        if prefer_project && path.file_name().is_some_and(|n| n == "blueprint.json") {
            let bundled = path.with_file_name("circuit.project.json");
            if bundled.is_file() {
                let mut circuit = Self::project(json(&bundled)?, bundled)?;
                circuit.attach_map(&path.with_file_name("circuit-map.json"), false);
                circuit.report =
                    "Bundled source diagram • physical-only export relays are excluded".into();
                return Ok(circuit);
            }
        }
        let data = json(&path)?;
        if data.get("format").and_then(Value::as_str) == Some("scrap-circuit") {
            let mut circuit = Self::project(data, path.clone())?;
            if path
                .file_name()
                .is_some_and(|n| n == "circuit.project.json")
            {
                circuit.attach_map(&path.with_file_name("circuit-map.json"), false);
            }
            Ok(circuit)
        } else if data.get("bodies").is_some() {
            Self::blueprint(data, path)
        } else {
            Err("Choose a scrap-circuit v1 project or a version-4 blueprint.json".into())
        }
    }

    pub fn empty() -> Self {
        Self::build(
            "No circuit loaded".into(),
            Vec::new(),
            Vec::new(),
            PathBuf::new(),
            "No circuit loaded".into(),
            true,
        )
        .unwrap()
    }

    fn project(data: Value, path: PathBuf) -> Result<Self, String> {
        let project: Project =
            serde_json::from_value(data).map_err(|e| format!("Invalid circuit project: {e}"))?;
        if project.format != "scrap-circuit" || project.version != 1 {
            return Err("Only scrap-circuit version 1 is supported".into());
        }
        let edges = project.edges.into_iter().map(|[a, b]| (a, b)).collect();
        Self::build(
            project.name,
            project.nodes,
            edges,
            path,
            "Source diagram • saved initial signals".into(),
            true,
        )
    }

    fn blueprint(data: Value, path: PathBuf) -> Result<Self, String> {
        if data["version"].as_u64() != Some(4) {
            return Err("Only version-4 game blueprints are supported".into());
        }
        let mut nodes = Vec::new();
        let mut links = Vec::new();
        let mut rows = Vec::new();
        let mut ignored = 0;
        let bodies = data["bodies"]
            .as_array()
            .ok_or("Blueprint bodies must be an array")?;
        for (body_index, body) in bodies.iter().enumerate() {
            let children = body["childs"]
                .as_array()
                .ok_or("Each body needs a childs array")?;
            for child in children {
                let ct = &child["controller"];
                let kind = match child["shapeId"].as_str() {
                    Some(GATE) => ct["mode"].as_u64().and_then(Kind::mode),
                    Some(SWITCH) => Some(Kind::Input),
                    _ => None,
                };
                let Some(kind) = kind else {
                    ignored += 1;
                    continue;
                };
                let id = ct["id"]
                    .as_u64()
                    .ok_or("Logic controller is missing a non-negative integer ID")?;
                let pos = ["x", "y", "z"].map(|axis| child["pos"][axis].as_i64());
                let [Some(x), Some(y), Some(z)] = pos else {
                    return Err(format!("Controller {id} has an invalid position"));
                };
                if let Some(targets) = ct["controllers"].as_array() {
                    for target in targets {
                        links.push((
                            id,
                            target["id"].as_u64().ok_or("Invalid wire controller ID")?,
                        ));
                    }
                } else if !ct["controllers"].is_null() {
                    return Err("Controller connections must be an array or null".into());
                }
                rows.push((body_index, z, y, x, nodes.len()));
                nodes.push(Node {
                    id,
                    kind,
                    x: 0.,
                    y: 0.,
                    label: format!("{} [{id}]", kind.name()),
                    initial: ct["active"].as_bool().unwrap_or(false),
                    controller_id: Some(id),
                    physical: Some([x, y, z]),
                    physical_only: false,
                });
            }
        }
        // Each body and physical layer gets its own diagram row. Coordinates stay available in the inspector.
        rows.sort_unstable();
        let mut last = None;
        let (mut row, mut col) = (0, 0);
        for (body, z, y, _, index) in rows {
            let key = (body, z, y);
            if last != Some(key) {
                if last.is_some() {
                    row += 1;
                }
                col = 0;
                last = Some(key);
            }
            nodes[index].x = 80. + col as f32 * 180.;
            nodes[index].y = 80. + row as f32 * 110.;
            col += 1;
        }
        let name = json(&path.with_file_name("description.json"))
            .ok()
            .and_then(|v| v["name"].as_str().map(str::to_owned))
            .unwrap_or_else(|| {
                path.parent()
                    .and_then(Path::file_name)
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned()
            });
        let mut circuit = Self::build(name, nodes, links, path.clone(), String::new(), false)?;
        let dropped = circuit.report.clone();
        circuit.report = format!(
            "Physical wiring projection • {ignored} unsupported shapes omitted • {dropped}"
        );
        circuit.attach_map(&path.with_file_name("circuit-map.json"), true);
        Ok(circuit)
    }

    fn attach_map(&mut self, path: &Path, physical: bool) {
        let Ok(value) = json(path) else {
            return;
        };
        let Some(entries) = value["nodes"].as_array() else {
            return;
        };
        let indices: HashMap<_, _> = self
            .nodes
            .iter()
            .enumerate()
            .map(|(i, n)| (n.id, i))
            .collect();
        for entry in entries {
            if !physical && entry["physicalOnly"] == true {
                continue;
            }
            let key = if physical { "controllerId" } else { "editorId" };
            let Some(index) = entry[key].as_u64().and_then(|id| indices.get(&id).copied()) else {
                continue;
            };
            let node = &mut self.nodes[index];
            node.controller_id = entry["controllerId"].as_u64();
            node.physical_only = entry["physicalOnly"].as_bool().unwrap_or(false);
            if let [Some(x), Some(y), Some(z)] =
                ["x", "y", "z"].map(|a| entry["position"][a].as_i64())
            {
                node.physical = Some([x, y, z]);
            }
            if physical {
                if let Some(label) = entry["label"].as_str() {
                    node.label = label.into();
                }
                if entry["kind"] == "OUTPUT" && node.kind == Kind::Or {
                    node.kind = Kind::Output;
                }
            }
        }
    }

    fn build(
        name: String,
        nodes: Vec<Node>,
        links: Vec<(u64, u64)>,
        source: PathBuf,
        mut report: String,
        strict: bool,
    ) -> Result<Self, String> {
        if nodes.is_empty() && !strict {
            return Err("No supported vanilla logic gates or switches in this blueprint".into());
        }
        let mut ids = HashMap::new();
        let mut bounds = [
            f32::INFINITY,
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::NEG_INFINITY,
        ];
        let mut spatial: HashMap<_, Vec<usize>> = HashMap::new();
        for (i, node) in nodes.iter().enumerate() {
            if (strict && node.id == 0) || ids.insert(node.id, i).is_some() {
                return Err("Node IDs must be unique (and positive in projects)".into());
            }
            if !node.x.is_finite()
                || !node.y.is_finite()
                || node.x.abs() > 1e9
                || node.y.abs() > 1e9
            {
                return Err("Node positions must be finite and within ±1 billion".into());
            }
            bounds[0] = bounds[0].min(node.x);
            bounds[1] = bounds[1].min(node.y);
            bounds[2] = bounds[2].max(node.x + 140.);
            bounds[3] = bounds[3].max(node.y + 64.);
            spatial
                .entry((
                    (node.x / 256.).floor() as i32,
                    (node.y / 256.).floor() as i32,
                ))
                .or_default()
                .push(i);
        }
        if nodes.is_empty() {
            bounds = [0., 0., 400., 300.];
        }
        let mut edges = BTreeSet::new();
        let mut dropped = 0;
        for (a, b) in links {
            let valid = ids
                .get(&a)
                .zip(ids.get(&b))
                .filter(|(i, j)| i != j && nodes[**j].kind != Kind::Input);
            if let Some((&i, &j)) = valid {
                edges.insert((i, j));
            } else if strict {
                return Err(format!(
                    "Invalid wire {a} → {b}: missing node, self-link or input destination"
                ));
            } else {
                dropped += 1;
            }
        }
        if !strict {
            report = format!("{dropped} unsupported wires omitted");
        }
        let mut incoming = vec![Vec::new(); nodes.len()];
        let mut outgoing = incoming.clone();
        let edges: Vec<_> = edges.into_iter().collect();
        for &(a, b) in &edges {
            incoming[b].push(a);
            outgoing[a].push(b);
        }
        let states = nodes.iter().map(|n| n.initial).collect();
        let scratch = vec![false; nodes.len()];
        Ok(Self {
            name,
            nodes,
            edges,
            incoming,
            outgoing,
            states,
            scratch,
            ticks: 0,
            source,
            report,
            bounds,
            spatial,
        })
    }

    pub fn reset(&mut self) {
        for (state, node) in self.states.iter_mut().zip(&self.nodes) {
            *state = node.initial;
        }
        self.ticks = 0;
    }

    pub fn step(&mut self) -> bool {
        let mut changed = false;
        for (i, node) in self.nodes.iter().enumerate() {
            let inputs = &self.incoming[i];
            let on = inputs.iter().filter(|&&j| self.states[j]).count();
            let value = if node.kind == Kind::Input {
                self.states[i]
            } else if inputs.is_empty() {
                false
            } else {
                match node.kind {
                    Kind::And => on == inputs.len(),
                    Kind::Or | Kind::Output => on > 0,
                    Kind::Xor => on % 2 == 1,
                    Kind::Nand => on != inputs.len(),
                    Kind::Nor => on == 0,
                    Kind::Xnor => on % 2 == 0,
                    Kind::Input => unreachable!(),
                }
            };
            changed |= value != self.states[i];
            self.scratch[i] = value;
        }
        std::mem::swap(&mut self.states, &mut self.scratch);
        self.ticks += 1;
        changed
    }

    pub fn signature(&self) -> Vec<u64> {
        self.states
            .chunks(64)
            .map(|chunk| {
                chunk
                    .iter()
                    .enumerate()
                    .fold(0, |bits, (i, &on)| bits | ((on as u64) << i))
            })
            .collect()
    }

    /// Graph traversal uses dense indices and handles cycles without recursion.
    pub fn trace(&self, start: usize, upstream: bool) -> HashSet<usize> {
        let mut found = HashSet::from([start]);
        let mut pending = vec![start];
        let graph = if upstream {
            &self.incoming
        } else {
            &self.outgoing
        };
        while let Some(i) = pending.pop() {
            for &next in &graph[i] {
                if found.insert(next) {
                    pending.push(next);
                }
            }
        }
        found
    }

    pub fn visible_nodes(&self, rect: [f32; 4]) -> Vec<usize> {
        let x0 = ((rect[0] - 140.) / 256.).floor() as i32;
        let y0 = ((rect[1] - 64.) / 256.).floor() as i32;
        let x1 = (rect[2] / 256.).floor() as i32;
        let y1 = (rect[3] / 256.).floor() as i32;
        let intersects = |n: &Node| {
            n.x + 140. >= rect[0] && n.y + 64. >= rect[1] && n.x <= rect[2] && n.y <= rect[3]
        };
        let mut visible = Vec::new();
        if (x1 as i64 - x0 as i64 + 1) * (y1 as i64 - y0 as i64 + 1) > self.spatial.len() as i64 * 2
        {
            visible.extend(
                self.nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| intersects(n))
                    .map(|(i, _)| i),
            );
        } else {
            for x in x0..=x1 {
                for y in y0..=y1 {
                    if let Some(indices) = self.spatial.get(&(x, y)) {
                        visible.extend(
                            indices
                                .iter()
                                .copied()
                                .filter(|&i| intersects(&self.nodes[i])),
                        );
                    }
                }
            }
        }
        visible.sort_unstable();
        visible
    }
}

#[derive(Clone)]
pub struct LibraryEntry {
    pub name: String,
    pub path: PathBuf,
}
pub fn default_library() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_default();
    let users = base.join("Axolot Games/Scrap Mechanic/User");
    // Discover an existing account rather than depending on one machine's Steam ID.
    fs::read_dir(&users)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .map(|e| e.path().join("Blueprints"))
        .find(|p| p.is_dir())
        .unwrap_or(users)
}
pub fn scan_library(root: &Path) -> Result<Vec<LibraryEntry>, String> {
    let mut entries = Vec::new();
    for dir in fs::read_dir(root).map_err(|e| format!("{}: {e}", root.display()))? {
        let path = dir.map_err(|e| e.to_string())?.path();
        if !path.join("blueprint.json").is_file() {
            continue;
        }
        let name = json(&path.join("description.json"))
            .ok()
            .and_then(|v| v["name"].as_str().map(str::to_owned))
            .unwrap_or_else(|| {
                path.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned()
            });
        entries.push(LibraryEntry { name, path });
    }
    entries.sort_by_cached_key(|e| e.name.to_lowercase());
    Ok(entries)
}
