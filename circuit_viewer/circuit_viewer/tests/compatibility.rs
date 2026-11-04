use circuit_viewer::model::{Circuit, Kind, scan_library};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
};

static SEQUENCE: AtomicUsize = AtomicUsize::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "circuit-viewer-test-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn write(&self, name: &str, value: &Value) -> PathBuf {
        let path = self.0.join(name);
        fs::write(&path, serde_json::to_vec(value).unwrap()).unwrap();
        path
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn node(id: u64, kind: &str) -> Value {
    json!({"id":id,"kind":kind,"label":format!("{kind} {id}"),"x":(id as f64)*180.,"y":80.,"initial":false})
}
fn project(nodes: Vec<Value>, edges: Vec<[u64; 2]>) -> Value {
    json!({"format":"scrap-circuit","version":1,"name":"Test","nodes":nodes,"edges":edges})
}
fn settle(c: &mut Circuit) {
    for _ in 0..64 {
        if !c.step() {
            return;
        }
    }
    panic!("Circuit did not settle");
}
#[test]
fn parallel_ticks_parity_disconnected_gates_and_reset() {
    let fixture = Fixture::new();
    let kinds = ["AND", "OR", "XOR", "NAND", "NOR", "XNOR", "OUTPUT"];
    let mut nodes = vec![node(1, "INPUT"), node(2, "INPUT"), node(3, "INPUT")];
    let mut edges = Vec::new();
    for (i, kind) in kinds.iter().enumerate() {
        let id = i as u64 + 4;
        nodes.push(node(id, kind));
        nodes.push(node(id + 20, kind));
        edges.extend([[1, id], [2, id], [3, id]]);
    }
    let mut c = Circuit::load(&fixture.write("test.json", &project(nodes, edges)), true).unwrap();
    for mask in 0u32..8 {
        c.reset();
        for bit in 0..3 {
            c.states[bit] = mask & (1 << bit) != 0;
        }
        c.step();
        let on = mask.count_ones();
        for (kind, expected) in kinds.iter().zip([
            on == 3,
            on > 0,
            on % 2 == 1,
            on != 3,
            on == 0,
            on % 2 == 0,
            on > 0,
        ]) {
            let i = c
                .nodes
                .iter()
                .position(|n| n.kind.name() == *kind && n.id < 20)
                .unwrap();
            assert_eq!(c.states[i], expected, "{kind}, {mask}");
        }
        for (i, n) in c.nodes.iter().enumerate().filter(|(_, n)| n.id >= 20) {
            assert!(!c.states[i], "Disconnected {} is off", n.kind.name());
        }
    }
    c.reset();
    assert_eq!(c.ticks, 0);
    assert!(c.states.iter().all(|s| !s));
    let chain = project(
        vec![node(1, "INPUT"), node(2, "OR"), node(3, "OUTPUT")],
        vec![[1, 2], [2, 3]],
    );
    let mut c = Circuit::load(&fixture.write("chain.json", &chain), true).unwrap();
    c.states[0] = true;
    c.step();
    assert_eq!(c.states, vec![true, true, false]);
    c.step();
    assert_eq!(c.states, vec![true, true, true]);
}

#[test]
fn cycles_trace_and_saved_initial_signals() {
    let fixture = Fixture::new();
    let mut c = Circuit::load(
        &fixture.write(
            "oscillator.json",
            &project(vec![node(1, "NOR"), node(2, "NOR")], vec![[1, 2], [2, 1]]),
        ),
        true,
    )
    .unwrap();
    let start = c.signature();
    c.step();
    let next = c.signature();
    assert_ne!(start, next);
    c.step();
    assert_eq!(start, c.signature());
    let mut n = node(1, "INPUT");
    n["initial"] = true.into();
    let mut c = Circuit::load(
        &fixture.write("initial.json", &project(vec![n], vec![])),
        true,
    )
    .unwrap();
    assert!(c.states[0]);
    c.states[0] = false;
    c.reset();
    assert!(c.states[0]);
}

#[test]
fn invalid_projects_are_rejected_with_empty_and_duplicate_edges_handled() {
    let fixture = Fixture::new();
    let mut bad_version = project(vec![], vec![]);
    bad_version["version"] = 9.into();
    for invalid in [
        bad_version,
        project(vec![node(0, "OR")], vec![]),
        project(vec![node(1, "OR"), node(1, "OR")], vec![]),
        project(vec![node(1, "OR")], vec![[1, 99]]),
        project(vec![node(1, "OR")], vec![[1, 1]]),
        project(vec![node(1, "OR"), node(2, "INPUT")], vec![[1, 2]]),
        project(vec![node(1, "TIMER")], vec![]),
    ] {
        assert!(Circuit::load(&fixture.write("invalid.json", &invalid), true).is_err());
    }
    let empty =
        Circuit::load(&fixture.write("empty.json", &project(vec![], vec![])), true).unwrap();
    assert_eq!(empty.nodes.len(), 0);
    let c = Circuit::load(
        &fixture.write(
            "dedup.json",
            &project(
                vec![node(1, "INPUT"), node(2, "OUTPUT")],
                vec![[1, 2], [1, 2]],
            ),
        ),
        true,
    )
    .unwrap();
    assert_eq!(c.edges.len(), 1);
}

fn blueprint() -> Value {
    json!({"version":4,"bodies":[{"childs":[
        {"shapeId":"7cf717d7-d167-4f2d-a6e7-6b2c70aa3986","pos":{"x":1,"y":0,"z":1},"controller":{"id":0,"active":true,"controllers":[{"id":9},{"id":55}]}},
        {"shapeId":"9f0f56e8-2c31-4d83-996c-d00a9b296c3f","pos":{"x":2,"y":0,"z":1},"controller":{"id":9,"mode":1,"controllers":[{"id":0}]}},
        {"shapeId":"unsupported-timer","controller":{"id":55}}
    ]},{"childs":[{"shapeId":"9f0f56e8-2c31-4d83-996c-d00a9b296c3f","pos":{"x":1,"y":0,"z":1},"controller":{"id":10,"mode":4,"controllers":null}}]}]})
}

#[test]
fn native_blueprint_direction_omissions_layers_bom_and_metadata() {
    let f = Fixture::new();
    let path = f.write("blueprint.json", &blueprint());
    f.write("description.json", &json!({"name":"Physical test"}));
    f.write("circuit-map.json",&json!({"nodes":[{"controllerId":9,"editorId":27,"kind":"OUTPUT","label":"Result","position":{"x":2,"y":0,"z":1},"physicalOnly":true}]}));
    let mut bytes = vec![0xef, 0xbb, 0xbf];
    bytes.extend(fs::read(&path).unwrap());
    fs::write(&path, bytes).unwrap();
    let mut c = Circuit::load(&f.0, false).unwrap();
    assert_eq!(c.name, "Physical test");
    assert_eq!(c.nodes.len(), 3);
    assert_eq!(c.edges, vec![(0, 1)]);
    assert!(c.report.contains("1 unsupported shapes"));
    assert!(c.report.contains("2 unsupported wires"));
    assert_eq!(c.nodes[1].label, "Result");
    assert_eq!(c.nodes[1].kind, Kind::Output);
    assert!(c.nodes[1].physical_only);
    assert_eq!(c.nodes[0].controller_id, Some(0));
    assert_ne!(c.nodes[0].y, c.nodes[2].y);
    c.step();
    assert!(c.states[1]);
    assert!(!c.states[2]);
}

#[test]
fn bundled_source_preference_and_library_names() {
    let f = Fixture::new();
    let export = f.0.join("export-uuid");
    fs::create_dir(&export).unwrap();
    fs::write(
        export.join("blueprint.json"),
        serde_json::to_vec(&blueprint()).unwrap(),
    )
    .unwrap();
    let source = project(vec![node(27, "OUTPUT")], vec![]);
    fs::write(
        export.join("circuit.project.json"),
        serde_json::to_vec(&source).unwrap(),
    )
    .unwrap();
    fs::write(
        export.join("description.json"),
        br#"{"name":"Readable library name"}"#,
    )
    .unwrap();
    fs::write(export.join("circuit-map.json"),br#"{"nodes":[{"editorId":27,"controllerId":9,"position":{"x":3,"y":4,"z":5},"physicalOnly":false}]}"#).unwrap();
    let bundled = Circuit::load(&export, true).unwrap();
    assert_eq!(bundled.nodes.len(), 1);
    assert_eq!(bundled.nodes[0].controller_id, Some(9));
    assert_eq!(bundled.nodes[0].physical, Some([3, 4, 5]));
    assert_eq!(Circuit::load(&export, false).unwrap().nodes.len(), 3);
    let library = scan_library(&f.0).unwrap();
    assert_eq!(library.len(), 1);
    assert_eq!(library[0].name, "Readable library name");
    assert_eq!(library[0].path, export);
}

#[test]
fn spatial_index_matches_brute_force_for_sparse_and_negative_positions() {
    let f = Fixture::new();
    let nodes = (1..=3000)
        .map(|id| {
            let mut n = node(id, "OR");
            n["x"] = ((id % 100) as f32 * 180. - 9000.).into();
            n["y"] = ((id / 100) as f32 * 110. - 1000.).into();
            n
        })
        .collect();
    let c = Circuit::load(&f.write("large.json", &project(nodes, vec![])), true).unwrap();
    for r in [
        [-10000., -2000., 10000., 10000.],
        [-10., -10., 10., 10.],
        [-500., -500., 500., 500.],
        [50000., 50000., 51000., 51000.],
    ] {
        let expected: Vec<_> = c
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, n)| n.x + 140. >= r[0] && n.y + 64. >= r[1] && n.x <= r[2] && n.y <= r[3])
            .map(|(i, _)| i)
            .collect();
        assert_eq!(c.visible_nodes(r), expected);
    }
}
