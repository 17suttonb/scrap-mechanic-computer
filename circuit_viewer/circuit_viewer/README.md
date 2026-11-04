# Circuit Workshop — Rust viewer

A native desktop viewer for existing Circuit Workshop projects and Scrap Mechanic logic blueprints. Circuit topology and source files are read-only; signal changes live only in memory. The existing Python editor remains available separately.

Double-click **Launch Circuit Viewer.bat** in the parent folder. It builds the current source and opens the viewer. The first build downloads Rust dependencies; later builds use Cargo's cache. Rust 1.85 or newer and the Windows MSVC C++ build tools are required to build. After building, `target/release/circuit-viewer.exe` runs independently of Python, Cargo and the repository.

```powershell
cargo run --release --locked --manifest-path circuit_viewer/Cargo.toml
cargo run --release --locked --manifest-path circuit_viewer/Cargo.toml -- path/to/project.circuit.json
cargo run --release --locked --manifest-path circuit_viewer/Cargo.toml -- --physical "path/to/blueprint-folder"
```

Open `.circuit.json`, `circuit.project.json` or `blueprint.json`, or drop a file/blueprint folder onto the window. **Blueprint library** discovers a local game's account folder and lists exports by their saved names. **Choose folder** switches accounts or opens another library. Parsing and library scans run in background threads.

Exports with `circuit.project.json` open their bundled source diagram by default, retaining labels and original positions. Turn off **Prefer bundled source diagram** in Library before opening to inspect the actual serialized physical wiring, including export-only feedback relays. `--physical` starts with that preference off. Physical wiring projects each body/layer into diagram rows and reads `circuit-map.json` when available to restore labels, controller IDs and coordinates. Native timers, buttons, sensors, unsupported shapes and links involving them are omitted and reported. The 3D option arranges the logic graph from its connections; physical coordinates remain available in the inspector.

| Control | Action |
| --- | --- |
| Cluster map | Static hierarchy; selected automatically when opening 15,000+ blocks |
| Click / double-click a cluster | Inspect / expand into smaller groups |
| Up / Backspace / breadcrumb | Navigate to a parent or whole circuit |
| Link limit | Bound drawn links; strongest eligible connections appear first |
| Diagram / Force-directed / 3D force | Saved positions / planar force layout / spatial force layout |
| Freeze / Relax / Restart layout | Stop layout movement / run forces again / start from a fresh spread |
| Spawn in waves / Reveal all | Restart with progressive spawning / admit the remaining nodes immediately |
| Auto-fit layout | Keep the changing graph framed; manual navigation disables this |
| Drag canvas / mouse wheel | Pan / zoom in the planar views |
| Drag in 3D | Orbit around the camera target |
| Right / middle / Shift+drag in 3D | Pan the camera target |
| Wheel / Reset camera in 3D | Move closer or farther / restore the viewing angle and frame the graph |
| F / Fit | Frame the whole circuit |
| Click component | Inspect states, IDs, coordinates and direct connections |
| Double-click gate / search result | Center on a component |
| Ctrl+F | Search labels, source IDs, or `c:123` controller IDs |
| I/O tab / double-click input | Toggle a switch in the preview |
| Upstream / Downstream | Trace a connected path, including feedback loops |
| Selected / Trace / All wires | Control connection visibility |
| Click or drag overview | Navigate large planar diagrams; 3D has an orientation compass |
| Space / N / R | Run or pause / step / reset preview |
| Ctrl+O / Esc | Open / clear selection and pause |

Large circuits start with Selected wires above 4,000 connections. The canvas uses spatial indexing, viewport culling and reduced detail when zoomed out; lists use virtual rows. The corner overview samples up to roughly 2,000–4,000 components. All-wires rendering still visits the entire connection graph and can be expensive on very large CPUs.

**Cluster map** is recommended for large circuits and is selected automatically when opening 15,000 or more blocks. It uses stable tiles with no physics simulation. Connectivity is partitioned into groups of at most 64 gates and folded into a hierarchy with at most 32 root tiles and 16 subclusters per intermediate page. Broad clock/control fanout is deprioritized during local grouping. These are automatic connectivity clusters; they do not reconstruct HDL module boundaries. Labels remain as navigation hints.

Click a cluster for exact block, I/O, internal-wire and boundary-wire counts, preview ON counts, and a virtual list of components. Double-click or use **Open cluster** to drill down. **Up**, **Backspace** and breadcrumbs return to larger scopes. Global search and inspector links jump directly into the containing leaf and center the actual gate, including gates outside the current page. Saved positions and topology stay intact.

Repeated directed connections between tiles are bundled into counted links. Width indicates bundle size; green indicates at least one ON source. Hover for exact wire and active-source counts. Selected wires show immediate connections for a tile. A selected gate traces the full circuit; a selected cluster traces the aggregate graph within the current page. **All** considers every bundle, with a **Link limit** (8–256, initially 64) for readability. The footer reports drawn/eligible links and external boundary-wire totals. Hidden links remain represented in counts and the inspector.

Hierarchy construction runs with file parsing in the background. Pages and bundles are built only on navigation; rendering visits at most 64 tiles and cached page links. ON counts update with the Boolean preview. The diagram and force views remain available for spatial exploration.

Both force views use damped movement, degree-aware wire attraction, node repulsion and weak centering for disconnected groups. A Barnes–Hut quadtree (2D) or octree (3D) approximates distant forces. The approximation adapts to graph size while nearby interactions stay exact. Computation runs in a cancellable background thread with a bounded snapshot buffer, cooling over 720 iterations before freezing automatically.

**Spawn in waves** starts from roots in each connected component, preferring circuit inputs, then admits breadth-first batches near parents that are already visible. Up to 24 waves progressively expand the graph and allow existing neighborhoods to relax. Wave and spawned-node counts appear in the toolbar. Pending components remain searchable: locating one reveals the remaining graph and centers that component. **Reveal all** skips the remaining waves; **Relax layout** resumes a frozen arrangement and admits pending nodes; **Restart layout** starts the sequence again. Uncheck **Spawn in waves** to start with the entire graph.

**3D force** simulates all three coordinates and renders a perspective view with gate-colored spheres, depth cues, directed edges and an XYZ compass. Green rings show ON signals. Picking selects the nearest visible sphere under the pointer. Search results and inspector links center the camera on the component; hovering shows full labels. Labels are reduced in dense views. Orbit, pan and zoom disable auto-fit so navigation stays under your control. Freeze the layout to inspect a stationary region. **Reset camera** restores the starting orientation; **F** frames the current arrangement while preserving orientation.

Search, selection, tracing and Boolean preview work across all three views. **Diagram** restores saved positions. The force views preserve source files, node IDs, connections, initial states and preview signals. Layout and signal simulation have independent controls; Escape freezes both.

The optional simulation evaluates all gates simultaneously from the preceding tick, matching `circuit_core.py`. Disconnected gates are off; XOR/XNOR use parity; OUTPUT behaves as OR. Run advances at four ticks per second. Settle runs incrementally without blocking the interface and stops at stability, a repeated state, or 512 ticks. Reset restores saved initial signals. This Boolean preview does not emulate game scheduling, physics, native timers or lift startup.

Validation:

Hierarchy tests cover complete node coverage, bounded pages, disconnected circuits, feedback, deterministic grouping, exact bundled/boundary-wire accounting and preview counts on graphs up to 20,000 blocks.

```powershell
cargo test --locked --manifest-path circuit_viewer/Cargo.toml
cargo clippy --locked --manifest-path circuit_viewer/Cargo.toml --all-targets -- -D warnings
cargo run --locked --manifest-path circuit_viewer/Cargo.toml -- --check projects/full-adder.circuit.json
cargo run --locked --manifest-path circuit_viewer/Cargo.toml -- --check --physical "path/to/blueprint-folder"
```

`--check` accepts multiple paths, prints counts/import reports, opens no window, and returns a failure exit code if any file cannot load. Tests cover the full and four-bit ripple adder truth tables, parallel ticks, all gate truth functions, disconnected gates, reset, cyclic traversal, invalid projects, native imports, metadata, bundled-source preference, library names and spatial culling. Layout tests compare approximate repulsion with exact forces in both dimensions and exercise wave order, pending-node reveal, disconnected groups, cycles, coincident nodes and a 20,000-node 3D graph with high fanout. Camera tests cover projection, near-plane clipping, fitting active nodes, orbit, pan and zoom. UI uses [eframe/egui](https://docs.rs/eframe/0.31.1/eframe/) with the native OpenGL renderer.
