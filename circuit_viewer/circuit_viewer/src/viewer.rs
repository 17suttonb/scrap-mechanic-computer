use circuit_viewer::camera::Camera;
use circuit_viewer::layout::GraphView;
use circuit_viewer::model::{Circuit, KINDS, Kind, LibraryEntry, default_library, scan_library};
use circuit_viewer::overview::{Hierarchy, Item, LARGE_CIRCUIT, NONE, Page};
use circuit_viewer::physics::{ITERATIONS, Scene, WaveLayout, dot};
use eframe::egui::{
    self, Align2, Color32, FontId, Pos2, Rect, RichText, Sense, Stroke, StrokeKind, Vec2, pos2,
    vec2,
};
use std::{
    collections::HashSet,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::{Duration, Instant},
};

mod overview_ui;

const BG: Color32 = Color32::from_rgb(13, 20, 29);
const PANEL: Color32 = Color32::from_rgb(20, 30, 43);
const CARD: Color32 = Color32::from_rgb(30, 43, 59);
const MUTED: Color32 = Color32::from_rgb(143, 165, 187);
const ACCENT: Color32 = Color32::from_rgb(89, 222, 185);
const TEXT: Color32 = Color32::from_rgb(227, 237, 245);

#[derive(Clone, Copy, PartialEq)]
enum Wires {
    Off,
    Selected,
    Trace,
    All,
}
#[derive(Clone, Copy, PartialEq)]
enum Trace {
    Neighbors,
    Upstream,
    Downstream,
}
#[derive(Clone, Copy, PartialEq)]
enum Layout {
    Diagram,
    Force,
    Force3d,
    Overview,
}

struct LayoutUpdate {
    view: GraphView,
    scene: Scene,
    iteration: usize,
    done: bool,
}
struct LayoutTask {
    cancelled: Arc<AtomicBool>,
    // One replaceable snapshot bounds memory even when the UI is busy.
    latest: Arc<Mutex<Option<LayoutUpdate>>>,
    reveal: Arc<AtomicBool>,
}
impl Drop for LayoutTask {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Relaxed);
    }
}
enum Message {
    Loaded(u64, Result<Box<LoadedCircuit>, String>),
    Library(Result<Vec<LibraryEntry>, String>),
}
struct LoadedCircuit {
    circuit: Circuit,
    overview: Hierarchy,
}
struct Settling {
    seen: HashSet<Vec<u64>>,
    updates: usize,
}

pub struct Viewer {
    circuit: Circuit,
    tx: Sender<Message>,
    rx: Receiver<Message>,
    request: u64,
    loading: bool,
    selected: Option<usize>,
    trace: Trace,
    traced: HashSet<usize>,
    wires: Wires,
    search: String,
    kind: String,
    signal: usize,
    results: Vec<usize>,
    io: Vec<usize>,
    tab: usize,
    library_root: PathBuf,
    library: Vec<LibraryEntry>,
    library_search: String,
    scanning: bool,
    prefer_project: bool,
    query_focus: bool,
    zoom: f32,
    offset: Vec2,
    fit_next: bool,
    focus_next: Option<usize>,
    layout: Layout,
    overview: Hierarchy,
    overview_page: Page,
    overview_selected: Option<usize>,
    overview_focus_slot: Option<usize>,
    overview_counts: Vec<usize>,
    overview_marks: Vec<bool>,
    overview_trace_links: Vec<usize>,
    overview_link_budget: usize,
    graph: Option<GraphView>,
    scene: Option<Scene>,
    camera: Camera,
    spawn_waves: bool,
    layout_task: Option<LayoutTask>,
    layout_iteration: usize,
    follow_layout: bool,
    running: bool,
    last_tick: Instant,
    settling: Option<Settling>,
    status: String,
    error: Option<String>,
    show_help: bool,
}

impl Viewer {
    pub fn new(
        cc: &eframe::CreationContext<'_>,
        path: Option<PathBuf>,
        prefer_project: bool,
    ) -> Self {
        let mut visuals = egui::Visuals::dark();
        visuals.panel_fill = PANEL;
        visuals.window_fill = PANEL;
        visuals.extreme_bg_color = BG;
        visuals.faint_bg_color = CARD;
        visuals.override_text_color = Some(TEXT);
        visuals.selection.bg_fill = Color32::from_rgb(28, 92, 80);
        visuals.selection.stroke = Stroke::new(1., ACCENT);
        cc.egui_ctx.set_visuals(visuals);
        cc.egui_ctx.style_mut(|s| {
            s.spacing.item_spacing = vec2(8., 8.);
            s.spacing.button_padding = vec2(10., 6.);
        });
        let (tx, rx) = mpsc::channel();
        let circuit = Circuit::empty();
        let overview = Hierarchy::build(&circuit);
        let overview_page = overview.page(overview.root, &circuit);
        let overview_counts = overview.active_counts(&circuit.states);
        let overview_marks = vec![false; overview_page.items.len()];
        let overview_trace_links = vec![0; overview_page.bundles.len()];
        let mut app = Self {
            circuit,
            tx,
            rx,
            request: 0,
            loading: false,
            selected: None,
            trace: Trace::Neighbors,
            traced: HashSet::new(),
            wires: Wires::All,
            search: String::new(),
            kind: "All types".into(),
            signal: 0,
            results: Vec::new(),
            io: Vec::new(),
            tab: 0,
            library_root: default_library(),
            library: Vec::new(),
            library_search: String::new(),
            scanning: false,
            prefer_project,
            query_focus: false,
            zoom: 1.,
            offset: Vec2::ZERO,
            fit_next: true,
            focus_next: None,
            layout: Layout::Diagram,
            overview,
            overview_page,
            overview_selected: None,
            overview_focus_slot: None,
            overview_counts,
            overview_marks,
            overview_trace_links,
            overview_link_budget: 64,
            graph: None,
            scene: None,
            camera: Camera::default(),
            spawn_waves: true,
            layout_task: None,
            layout_iteration: 0,
            follow_layout: true,
            running: false,
            last_tick: Instant::now(),
            settling: None,
            status: "Open a circuit or browse your blueprint library.".into(),
            error: None,
            show_help: false,
        };
        app.refresh_results();
        app.refresh_io();
        if let Some(path) = path {
            app.load(path, &cc.egui_ctx);
        }
        app
    }

    fn load(&mut self, path: PathBuf, ctx: &egui::Context) {
        self.request += 1;
        let request = self.request;
        self.loading = true;
        self.layout_task = None;
        self.running = false;
        self.settling = None;
        self.status = format!("Opening {}…", path.display());
        let (tx, ctx, prefer) = (self.tx.clone(), ctx.clone(), self.prefer_project);
        std::thread::spawn(move || {
            let result = Circuit::load(&path, prefer).map(|circuit| {
                let overview = Hierarchy::build(&circuit);
                Box::new(LoadedCircuit { circuit, overview })
            });
            let _ = tx.send(Message::Loaded(request, result));
            ctx.request_repaint();
        });
    }

    fn open(&mut self, ctx: &egui::Context) {
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Open circuit project or blueprint.json")
            .add_filter("Circuit / blueprint", &["json"])
            .pick_file()
        {
            self.load(path, ctx);
        }
    }

    fn scan(&mut self, ctx: &egui::Context) {
        if self.scanning {
            return;
        }
        self.scanning = true;
        let (root, tx, ctx) = (self.library_root.clone(), self.tx.clone(), ctx.clone());
        std::thread::spawn(move || {
            let _ = tx.send(Message::Library(scan_library(&root)));
            ctx.request_repaint();
        });
    }

    fn refresh_io(&mut self) {
        self.io = self
            .circuit
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, n)| matches!(n.kind, Kind::Input | Kind::Output))
            .map(|(i, _)| i)
            .collect();
    }

    fn refresh_results(&mut self) {
        let query = self.search.trim().to_lowercase();
        self.results = self
            .circuit
            .nodes
            .iter()
            .enumerate()
            .filter(|(i, n)| {
                (self.kind == "All types" || n.kind.name() == self.kind)
                    && (self.signal == 0 || self.circuit.states[*i] == (self.signal == 1))
                    && (query.is_empty()
                        || n.label.to_lowercase().contains(&query)
                        || n.id.to_string() == query
                        || n.controller_id.is_some_and(|id| format!("c:{id}") == query))
            })
            .map(|(i, _)| i)
            .collect();
    }

    fn select(&mut self, index: usize, focus: bool) {
        if self.layout == Layout::Overview {
            self.overview_selected = None;
        }
        self.selected = Some(index);
        self.retrace();
        if focus {
            self.focus_next = Some(index);
            self.follow_layout = false;
        }
    }

    fn start_layout(&mut self, reset: bool, ctx: &egui::Context) {
        self.layout_task = None;
        let dimensions = if self.layout == Layout::Force3d { 3 } else { 2 };
        let reset = reset
            || self
                .scene
                .as_ref()
                .is_none_or(|s| s.dimensions != dimensions);
        let engine = if reset {
            let preferred: Vec<_> = self
                .circuit
                .nodes
                .iter()
                .map(|n| n.kind == Kind::Input)
                .collect();
            WaveLayout::new(
                self.circuit.nodes.len(),
                self.circuit.edges.clone(),
                dimensions,
                &preferred,
                self.spawn_waves,
            )
        } else {
            WaveLayout::resume(self.scene.clone().unwrap(), self.circuit.edges.clone())
        };
        let scene = engine.view();
        self.graph = Some(GraphView::from_active(&scene.positions, &scene.active));
        self.scene = Some(scene);
        if reset {
            self.follow_layout = true;
            self.fit_next = true;
        }
        self.layout_iteration = 0;
        if self.circuit.nodes.len() < 2 {
            return;
        }
        let cancelled = Arc::new(AtomicBool::new(false));
        let latest = Arc::new(Mutex::new(None));
        let reveal = Arc::new(AtomicBool::new(false));
        self.layout_task = Some(LayoutTask {
            cancelled: cancelled.clone(),
            latest: latest.clone(),
            reveal: reveal.clone(),
        });
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let mut engine = engine;
            let mut published = Instant::now();
            let mut batch = Instant::now();
            for iteration in 0..ITERATIONS {
                if cancelled.load(Ordering::Relaxed) {
                    return;
                }
                if reveal.swap(false, Ordering::Relaxed) {
                    engine.reveal_all();
                }
                engine.step(iteration);
                if (iteration + 1) % 8 == 0 {
                    // Small graphs animate rather than consuming a whole run in
                    // one frame. Expensive large-graph steps already pace themselves.
                    let remaining = Duration::from_millis(45).saturating_sub(batch.elapsed());
                    if !remaining.is_zero() {
                        std::thread::sleep(remaining);
                    }
                    batch = Instant::now();
                }
                let done = iteration + 1 == ITERATIONS;
                if done || published.elapsed() >= Duration::from_millis(50) {
                    let scene = engine.view();
                    let update = LayoutUpdate {
                        view: GraphView::from_active(&scene.positions, &scene.active),
                        scene,
                        iteration: iteration + 1,
                        done,
                    };
                    if cancelled.load(Ordering::Relaxed) {
                        return;
                    }
                    *latest.lock().unwrap() = Some(update);
                    ctx.request_repaint();
                    published = Instant::now();
                }
            }
        });
    }

    fn set_layout(&mut self, layout: Layout, ctx: &egui::Context) {
        self.layout = layout;
        self.fit_next = true;
        self.focus_next = None;
        if matches!(layout, Layout::Force | Layout::Force3d) {
            self.start_layout(false, ctx);
        } else {
            self.layout_task = None;
        }
        if layout == Layout::Overview {
            self.overview_selected = None;
            self.overview_refresh_trace();
        }
    }

    fn position(&self, i: usize) -> Vec2 {
        if self.layout == Layout::Force {
            if let Some(graph) = &self.graph {
                return Vec2::from(graph.positions[i]);
            }
        }
        let n = &self.circuit.nodes[i];
        vec2(n.x, n.y)
    }

    fn view_bounds(&self) -> [f32; 4] {
        if self.layout == Layout::Force {
            if let Some(graph) = &self.graph {
                return graph.bounds;
            }
        }
        self.circuit.bounds
    }

    fn node_active(&self, i: usize) -> bool {
        matches!(self.layout, Layout::Diagram | Layout::Overview)
            || self.scene.as_ref().is_none_or(|s| s.active[i])
    }

    fn reveal_pending(&mut self, ctx: &egui::Context) {
        if self.layout_task.is_none() {
            self.start_layout(false, ctx);
        }
        if let Some(task) = &self.layout_task {
            task.reveal.store(true, Ordering::Relaxed);
        }
    }

    fn retrace(&mut self) {
        self.traced.clear();
        if let Some(i) = self.selected {
            self.traced = match self.trace {
                Trace::Neighbors => self.circuit.incoming[i]
                    .iter()
                    .chain(&self.circuit.outgoing[i])
                    .copied()
                    .chain([i])
                    .collect(),
                Trace::Upstream => self.circuit.trace(i, true),
                Trace::Downstream => self.circuit.trace(i, false),
            };
        }
        self.overview_refresh_trace();
    }

    fn changed_signals(&mut self) {
        self.overview_counts = self.overview.active_counts(&self.circuit.states);
        self.overview_page.update_signals(&self.circuit.states);
        if self.signal != 0 {
            self.refresh_results();
        }
    }
    fn step(&mut self) {
        self.running = false;
        self.settling = None;
        let changed = self.circuit.step();
        self.status = if changed {
            "Advanced one parallel tick"
        } else {
            "Stable at this tick"
        }
        .into();
        self.changed_signals();
    }
    fn reset(&mut self) {
        self.running = false;
        self.settling = None;
        self.circuit.reset();
        self.changed_signals();
        self.status = "Preview reset to saved initial signals".into();
    }
    fn toggle(&mut self, index: usize) {
        if self.circuit.nodes[index].kind == Kind::Input {
            self.settling = None;
            self.circuit.states[index] = !self.circuit.states[index];
            self.changed_signals();
            self.status = format!(
                "{} → {} in preview",
                self.circuit.nodes[index].label,
                if self.circuit.states[index] {
                    "ON"
                } else {
                    "OFF"
                }
            );
        }
    }
    fn run_toggle(&mut self) {
        self.running = !self.running;
        self.settling = None;
        self.last_tick = Instant::now();
    }

    fn poll(&mut self, ctx: &egui::Context) {
        while let Ok(message) = self.rx.try_recv() {
            match message {
                Message::Loaded(request, result) if request == self.request => {
                    self.loading = false;
                    match result {
                        Ok(loaded) => {
                            let LoadedCircuit { circuit, overview } = *loaded;
                            self.circuit = circuit;
                            self.overview = overview;
                            self.overview_page =
                                self.overview.page(self.overview.root, &self.circuit);
                            self.overview_selected = None;
                            self.overview_focus_slot = None;
                            self.overview_counts =
                                self.overview.active_counts(&self.circuit.states);
                            self.overview_marks = vec![false; self.overview_page.items.len()];
                            self.overview_trace_links = vec![0; self.overview_page.bundles.len()];
                            if self.circuit.nodes.len() >= LARGE_CIRCUIT {
                                self.layout = Layout::Overview;
                            }
                            self.graph = None;
                            self.scene = None;
                            self.layout_task = None;
                            if matches!(self.layout, Layout::Force | Layout::Force3d) {
                                self.start_layout(true, ctx);
                            }
                            self.selected = None;
                            self.traced.clear();
                            self.focus_next = None;
                            self.search.clear();
                            self.kind = "All types".into();
                            self.signal = 0;
                            self.fit_next = true;
                            self.error = None;
                            self.wires = if self.circuit.edges.len() > 4000 {
                                Wires::Selected
                            } else {
                                Wires::All
                            };
                            self.status = self.circuit.report.clone();
                            self.refresh_results();
                            self.refresh_io();
                            ctx.send_viewport_cmd(egui::ViewportCommand::Title(format!(
                                "{} · Circuit Workshop Viewer",
                                self.circuit.name
                            )));
                        }
                        Err(error) => {
                            self.status =
                                "Could not open file. The current circuit is still available."
                                    .into();
                            self.error = Some(error);
                        }
                    }
                }
                Message::Library(result) => {
                    self.scanning = false;
                    match result {
                        Ok(entries) => self.library = entries,
                        Err(error) => self.error = Some(error),
                    }
                }
                _ => {}
            }
        }
        let update = self
            .layout_task
            .as_ref()
            .and_then(|task| task.latest.lock().unwrap().take());
        if let Some(update) = update {
            self.graph = Some(update.view);
            self.scene = Some(update.scene);
            self.layout_iteration = update.iteration;
            if self.follow_layout {
                self.fit_next = true;
            }
            if update.done {
                self.layout_task = None;
            }
        }
        if self.focus_next.is_some_and(|i| !self.node_active(i)) {
            self.reveal_pending(ctx);
        }
        if self.running && self.last_tick.elapsed() >= Duration::from_millis(250) {
            self.circuit.step();
            self.last_tick = Instant::now();
            self.changed_signals();
        }
        if let Some(mut settle) = self.settling.take() {
            let start = Instant::now();
            let mut done = false;
            while start.elapsed() < Duration::from_millis(8) {
                if !settle.seen.insert(self.circuit.signature()) {
                    self.status = format!(
                        "Repeated state after {} updates • feedback is oscillating",
                        settle.updates
                    );
                    done = true;
                    break;
                }
                settle.updates += 1;
                if !self.circuit.step() {
                    self.status = format!("Stable after {} updates", settle.updates);
                    done = true;
                    break;
                }
                if settle.updates >= 512 {
                    self.status = "Still changing after 512 updates • use Step to inspect".into();
                    done = true;
                    break;
                }
            }
            self.changed_signals();
            if !done {
                self.settling = Some(settle);
            }
        }
        if self.loading || self.scanning || self.settling.is_some() {
            ctx.request_repaint_after(Duration::from_millis(16));
        } else if self.running {
            ctx.request_repaint_after(Duration::from_millis(50));
        }
    }

    fn header(&mut self, ctx: &egui::Context) {
        egui::TopBottomPanel::top("header")
            .frame(egui::Frame::new().fill(PANEL).inner_margin(14.))
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    let (icon, painter) = ui.allocate_painter(vec2(24., 28.), Sense::hover());
                    let r = icon.rect.shrink2(vec2(4., 6.));
                    painter.rect_stroke(r, 3., Stroke::new(1.5, ACCENT), StrokeKind::Inside);
                    for y in [r.top() + 4., r.bottom() - 4.] {
                        for x in [r.left(), r.right()] {
                            painter.line_segment(
                                [pos2(x - 3., y), pos2(x + 3., y)],
                                Stroke::new(1.5, ACCENT),
                            );
                        }
                    }
                    ui.vertical(|ui| {
                        ui.label(RichText::new("CIRCUIT WORKSHOP").size(19.).strong());
                        ui.label(
                            RichText::new("SCRAP MECHANIC  /  RUST VIEWER")
                                .size(10.)
                                .color(MUTED),
                        );
                    });
                    ui.add_space(18.);
                    if ui.button("Open circuit…").clicked() {
                        self.open(ctx);
                    }
                    if ui.button("Blueprint library").clicked() {
                        self.tab = 2;
                        if self.library.is_empty() {
                            self.scan(ctx);
                        }
                    }
                    if ui.button("Fit  [F]").clicked() {
                        self.fit_next = true;
                    }
                    if ui
                        .button("?")
                        .on_hover_text("Controls and preview limitations")
                        .clicked()
                    {
                        self.show_help = true;
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.label(RichText::new("READ ONLY").size(11.).strong().color(ACCENT));
                        if self.loading {
                            ui.spinner();
                        }
                    });
                });
            });
        egui::TopBottomPanel::top("toolbar")
            .frame(egui::Frame::new().fill(BG).inner_margin(10.))
            .show(ctx, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new("View").color(MUTED));
                    let mut layout = self.layout;
                    ui.selectable_value(&mut layout, Layout::Diagram, "Diagram");
                    ui.selectable_value(&mut layout, Layout::Force, "Force-directed");
                    ui.selectable_value(&mut layout, Layout::Force3d, "3D force");
                    ui.selectable_value(&mut layout, Layout::Overview, "Cluster map")
                        .on_hover_text(
                            "Static hierarchy for 15,000+ blocks; drill into bounded groups",
                        );
                    if layout != self.layout {
                        self.set_layout(layout, ctx);
                    }
                    ui.separator();
                    ui.label(RichText::new("Wires").color(MUTED));
                    for (mode, label) in [
                        (Wires::Off, "Off"),
                        (Wires::Selected, "Selected"),
                        (Wires::Trace, "Trace"),
                        (Wires::All, "All"),
                    ] {
                        ui.selectable_value(&mut self.wires, mode, label);
                    }
                    ui.separator();
                    ui.label(RichText::new("Trace").color(MUTED));
                    let old = self.trace;
                    for (mode, label) in [
                        (Trace::Neighbors, "Neighbors"),
                        (Trace::Upstream, "Upstream"),
                        (Trace::Downstream, "Downstream"),
                    ] {
                        ui.selectable_value(&mut self.trace, mode, label);
                    }
                    if self.trace != old {
                        self.retrace();
                        self.wires = Wires::Trace;
                    }
                    ui.separator();
                    ui.add_enabled_ui(!self.loading, |ui| {
                        if ui
                            .button(if self.running { "Pause" } else { "Run" })
                            .clicked()
                        {
                            self.run_toggle();
                        }
                        if ui.button("Step").clicked() {
                            self.step();
                        }
                        if ui
                            .button(if self.settling.is_some() {
                                "Settling…"
                            } else {
                                "Settle"
                            })
                            .clicked()
                        {
                            self.running = false;
                            self.settling = Some(Settling {
                                seen: HashSet::new(),
                                updates: 0,
                            });
                        }
                        if ui.button("Reset").clicked() {
                            self.reset();
                        }
                    });
                });
            });
        if self.layout == Layout::Overview {
            self.overview_controls(ctx);
        }
        if matches!(self.layout, Layout::Force | Layout::Force3d) {
            egui::TopBottomPanel::top("layout_controls")
                .frame(egui::Frame::new().fill(PANEL).inner_margin(8.))
                .show(ctx, |ui| {
                    ui.horizontal_wrapped(|ui| {
                        ui.add_enabled_ui(!self.loading, |ui| {
                            if self.layout_task.is_some() {
                                if ui.button("Freeze layout").clicked() {
                                    self.layout_task = None;
                                }
                            } else if ui
                                .button("Relax layout")
                                .on_hover_text("Run the forces again from these positions")
                                .clicked()
                            {
                                self.start_layout(false, ctx);
                            }
                            if ui
                                .button("Restart layout")
                                .on_hover_text("Arrange the graph again from its initial spread")
                                .clicked()
                            {
                                self.start_layout(true, ctx);
                            }
                            if ui.checkbox(&mut self.spawn_waves,"Spawn in waves").on_hover_text("Apply on Restart: admit nodes breadth-first near existing neighbors").changed() { self.start_layout(true,ctx); }
                            if self.scene.as_ref().is_some_and(|s| s.active_count < s.positions.len()) && ui.button("Reveal all").clicked() { self.reveal_pending(ctx); }
                            if self.layout == Layout::Force3d && ui.button("Reset camera").clicked() { self.camera = Camera::default(); self.fit_next = true; }
                        });
                        if ui
                            .checkbox(&mut self.follow_layout, "Auto-fit layout")
                            .changed()
                            && self.follow_layout
                        {
                            self.fit_next = true;
                        }
                        let status = if self.layout_task.is_some() {
                            "Arranging"
                        } else {
                            "Frozen"
                        };
                        ui.label(
                            RichText::new(format!(
                                "{status} · {} / {ITERATIONS} iterations",
                                self.layout_iteration
                            ))
                            .small()
                            .color(MUTED),
                        );
                        if let Some(scene) = &self.scene {
                            ui.label(RichText::new(format!("Wave {}/{} · {}/{} nodes",scene.wave,scene.waves,scene.active_count,scene.positions.len())).small().color(ACCENT));
                        }
                        ui.label(
                            RichText::new("Layout changes only this view")
                                .small()
                                .color(MUTED),
                        );
                    });
                });
        }
    }

    fn sidebar(&mut self, ctx: &egui::Context) {
        egui::SidePanel::left("browser")
            .default_width(260.)
            .width_range(220. ..=420.)
            .show(ctx, |ui| {
                ui.add_space(12.);
                ui.label(RichText::new(&self.circuit.name).size(19.).strong());
                ui.label(
                    RichText::new(format!(
                        "{} blocks · {} wires",
                        self.circuit.nodes.len(),
                        self.circuit.edges.len()
                    ))
                    .color(MUTED),
                );
                ui.add_space(8.);
                ui.horizontal(|ui| {
                    for (i, label) in ["Components", "I/O", "Library"].iter().enumerate() {
                        if ui.selectable_value(&mut self.tab, i, *label).clicked()
                            && i == 2
                            && self.library.is_empty()
                        {
                            self.scan(ctx);
                        }
                    }
                });
                ui.separator();
                match self.tab {
                    0 => self.components(ui),
                    1 => self.io_panel(ui),
                    _ => self.library_panel(ui, ctx),
                }
            });
    }

    fn components(&mut self, ui: &mut egui::Ui) {
        let response = ui.add(
            egui::TextEdit::singleline(&mut self.search)
                .hint_text("Search label, ID or c:controller")
                .desired_width(f32::INFINITY),
        );
        if self.query_focus {
            response.request_focus();
            self.query_focus = false;
        }
        let mut changed = response.changed();
        ui.horizontal(|ui| {
            egui::ComboBox::from_id_salt("kind")
                .selected_text(&self.kind)
                .width(100.)
                .show_ui(ui, |ui| {
                    changed |= ui
                        .selectable_value(&mut self.kind, "All types".into(), "All types")
                        .changed();
                    for kind in KINDS {
                        changed |= ui
                            .selectable_value(&mut self.kind, kind.into(), kind)
                            .changed();
                    }
                });
            egui::ComboBox::from_id_salt("signal")
                .selected_text(["Any signal", "ON", "OFF"][self.signal])
                .width(78.)
                .show_ui(ui, |ui| {
                    for (i, label) in ["Any signal", "ON", "OFF"].iter().enumerate() {
                        changed |= ui.selectable_value(&mut self.signal, i, *label).changed();
                    }
                });
        });
        if changed {
            self.refresh_results();
        }
        ui.label(
            RichText::new(format!("{} results · click to locate", self.results.len()))
                .small()
                .color(MUTED),
        );
        let mut chosen = None;
        egui::ScrollArea::vertical()
            .id_salt("components")
            .auto_shrink([false, false])
            .show_rows(ui, 38., self.results.len(), |ui, rows| {
                for row in rows {
                    let i = self.results[row];
                    let n = &self.circuit.nodes[i];
                    let (rect, response) =
                        ui.allocate_exact_size(vec2(ui.available_width(), 38.), Sense::click());
                    let selected = self.selected == Some(i);
                    ui.painter().rect_filled(
                        rect,
                        4.,
                        if selected {
                            Color32::from_rgb(28, 92, 80)
                        } else if response.hovered() {
                            Color32::from_rgb(40, 57, 76)
                        } else {
                            CARD
                        },
                    );
                    let painter = ui.painter().with_clip_rect(rect.shrink(6.));
                    painter.text(
                        rect.min + vec2(9., 5.),
                        Align2::LEFT_TOP,
                        &n.label,
                        FontId::proportional(12.5),
                        TEXT,
                    );
                    painter.text(
                        rect.min + vec2(9., 22.),
                        Align2::LEFT_TOP,
                        format!("#{} · {}", n.id, n.kind.name()),
                        FontId::proportional(10.),
                        MUTED,
                    );
                    painter.text(
                        pos2(rect.right() - 9., rect.top() + 22.),
                        Align2::RIGHT_TOP,
                        if !self.node_active(i) {
                            "PENDING"
                        } else if self.circuit.states[i] {
                            "ON"
                        } else {
                            "OFF"
                        },
                        FontId::proportional(10.),
                        if self.circuit.states[i] {
                            ACCENT
                        } else {
                            MUTED
                        },
                    );
                    if response.on_hover_text(&n.label).clicked() {
                        chosen = Some(i);
                    }
                }
            });
        if let Some(i) = chosen {
            self.select(i, true);
        }
    }

    fn io_panel(&mut self, ui: &mut egui::Ui) {
        ui.label(RichText::new("INPUTS & OUTPUTS").small().color(MUTED));
        ui.label("Toggle switches to try signals in the preview.");
        if self.io.is_empty() {
            ui.label(RichText::new("No explicit I/O labels. Raw game imports identify switches; other gates retain their native type.").color(MUTED));
        }
        let (mut chosen, mut toggle) = (None, None);
        egui::ScrollArea::vertical()
            .id_salt("io")
            .show_rows(ui, 34., self.io.len(), |ui, rows| {
                for row in rows {
                    let i = self.io[row];
                    let node = &self.circuit.nodes[i];
                    ui.horizontal(|ui| {
                        let on = self.circuit.states[i];
                        if node.kind == Kind::Input {
                            if ui
                                .add(egui::Button::new(if on { "ON " } else { "OFF" }).fill(
                                    if on {
                                        Color32::from_rgb(28, 92, 80)
                                    } else {
                                        CARD
                                    },
                                ))
                                .clicked()
                            {
                                toggle = Some(i);
                            }
                        } else {
                            ui.label(RichText::new(if on { "ON" } else { "OFF" }).color(if on {
                                ACCENT
                            } else {
                                MUTED
                            }));
                        }
                        if ui
                            .selectable_label(self.selected == Some(i), &node.label)
                            .clicked()
                        {
                            chosen = Some(i);
                        }
                    });
                }
            });
        if let Some(i) = chosen {
            self.select(i, true);
        }
        if let Some(i) = toggle {
            self.toggle(i);
        }
    }

    fn library_panel(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        ui.label(RichText::new("BLUEPRINT LIBRARY").small().color(MUTED));
        ui.horizontal(|ui| {
            if ui
                .add_enabled(!self.scanning, egui::Button::new("Choose folder…"))
                .clicked()
            {
                if let Some(root) = rfd::FileDialog::new()
                    .set_directory(&self.library_root)
                    .pick_folder()
                {
                    self.library_root = root;
                    self.library.clear();
                    self.scan(ctx);
                }
            }
            if ui
                .add_enabled(!self.scanning, egui::Button::new("Refresh"))
                .clicked()
            {
                self.scan(ctx);
            }
        });
        ui.label(
            RichText::new(self.library_root.to_string_lossy())
                .small()
                .color(MUTED),
        );
        ui.checkbox(&mut self.prefer_project,"Prefer bundled source diagram").on_hover_text("Applied on the next open. Turn off to inspect exported physical wiring, including feedback relays.");
        ui.add(
            egui::TextEdit::singleline(&mut self.library_search)
                .hint_text("Filter blueprint names")
                .desired_width(f32::INFINITY),
        );
        if self.scanning {
            ui.spinner();
        }
        let query = self.library_search.to_lowercase();
        let entries: Vec<_> = self
            .library
            .iter()
            .enumerate()
            .filter(|(_, e)| e.name.to_lowercase().contains(&query))
            .map(|(i, _)| i)
            .collect();
        ui.label(
            RichText::new(format!("{} blueprints", entries.len()))
                .small()
                .color(MUTED),
        );
        let mut chosen = None;
        egui::ScrollArea::vertical().id_salt("library").show_rows(
            ui,
            36.,
            entries.len(),
            |ui, rows| {
                for row in rows {
                    let entry = &self.library[entries[row]];
                    if ui
                        .add_sized(
                            [ui.available_width(), 34.],
                            egui::Button::new(&entry.name).fill(CARD).truncate(),
                        )
                        .on_hover_text(entry.path.display().to_string())
                        .clicked()
                    {
                        chosen = Some(entry.path.clone());
                    }
                }
            },
        );
        if let Some(path) = chosen {
            self.load(path, ctx);
        }
    }

    fn inspector(&mut self, ctx: &egui::Context) {
        if self.layout == Layout::Overview
            && self
                .overview_selected
                .and_then(|slot| self.overview_page.items.get(slot))
                .is_some_and(|item| matches!(item, Item::Cluster(_)))
        {
            self.overview_inspector(ctx);
            return;
        }
        egui::SidePanel::right("inspector").default_width(248.).width_range(220. ..=400.).show(ctx,|ui| {
            ui.add_space(14.);
            ui.label(RichText::new("INSPECTOR").small().color(MUTED));
            let mut chosen = None;
            let mut toggle = None;
            egui::ScrollArea::vertical().id_salt("inspection").show(ui,|ui| {
                if let Some(i) = self.selected {
                    let node = &self.circuit.nodes[i];
                    ui.add_space(6.); ui.label(RichText::new(&node.label).size(21.).strong());
                    ui.label(RichText::new(format!("{}   #{}",node.kind.name(),node.id)).color(color(node.kind)));
                    ui.add_space(8.);
                    egui::Grid::new("properties").spacing([14.,8.]).show(ui,|ui| {
                        ui.label("Preview"); ui.label(RichText::new(if self.circuit.states[i] { "ON" } else { "OFF" }).color(if self.circuit.states[i] { ACCENT } else { MUTED })); ui.end_row();
                        ui.label("Saved initial"); ui.label(if node.initial { "ON" } else { "OFF" }); ui.end_row();
                        ui.label("Diagram"); ui.label(format!("{:.0}, {:.0}",node.x,node.y)); ui.end_row();
                        if self.layout == Layout::Force { let p = self.position(i); ui.label("Graph view"); ui.label(format!("{:.0}, {:.0}",p.x,p.y)); ui.end_row(); }
                        if self.layout == Layout::Force3d { if let Some(scene) = &self.scene { let p = scene.positions[i]; ui.label("Graph XYZ"); ui.label(format!("{:.0}, {:.0}, {:.0}",p[0],p[1],p[2])); ui.end_row(); } }
                        if !self.node_active(i) { ui.label("Layout"); ui.label("Pending wave"); ui.end_row(); }
                        if let Some(id) = node.controller_id { ui.label("Controller"); ui.label(id.to_string()); ui.end_row(); }
                        if let Some([x,y,z]) = node.physical { ui.label("Game XYZ"); ui.label(format!("{x}, {y}, {z}")); ui.end_row(); }
                    });
                    if node.physical_only { ui.label(RichText::new("Export-only feedback relay").color(ACCENT)); }
                    ui.add_space(8.);
                    if node.kind == Kind::Input && ui.button("Toggle preview input").clicked() { toggle = Some(i); }
                    if ui.button("Center on component").clicked() { self.focus_next = Some(i); }
                    ui.label(RichText::new(format!("{} components in trace",self.traced.len())).small().color(MUTED));
                    for (label,neighbors) in [("INCOMING",&self.circuit.incoming[i]),("OUTGOING",&self.circuit.outgoing[i])] {
                        ui.separator(); ui.label(RichText::new(format!("{label}  /  {}",neighbors.len())).small().color(MUTED));
                        for &j in neighbors.iter().take(100) {
                            let next = &self.circuit.nodes[j];
                            if ui.selectable_label(false,format!("{}  {}  #{}",if self.circuit.states[j] { "ON" } else { "OFF" },next.label,next.id)).clicked() { chosen = Some(j); }
                        }
                        if neighbors.len() > 100 { ui.label(RichText::new("First 100 connections shown").small().color(MUTED)); }
                    }
                } else {
                    ui.add_space(10.); ui.label(RichText::new("Follow the signal.").size(22.));
                    ui.label("Select a component to inspect its state and connections.");
                    ui.add_space(14.); ui.label(RichText::new("Choose Upstream or Downstream to follow a path through the circuit.").color(MUTED));
                    ui.separator();
                    for kind in KINDS { let k: Kind = serde_json::from_value(serde_json::Value::String(kind.into())).unwrap(); ui.label(RichText::new(kind).color(color(k))); }
                }
                ui.add_space(20.); ui.separator();
                ui.label(RichText::new("SOURCE").small().color(MUTED));
                ui.label(RichText::new(if self.circuit.source.as_os_str().is_empty() { "Built-in full adder".into() } else { self.circuit.source.display().to_string() }).small());
                ui.label(RichText::new(&self.circuit.report).small().color(MUTED));
            });
            if let Some(i) = chosen { self.select(i,true); }
            if let Some(i) = toggle { self.toggle(i); }
        });
    }

    fn fit(&mut self, rect: Rect) {
        let [x0, y0, x1, y1] = self.view_bounds();
        self.zoom = ((rect.width() - 80.).max(50.) / (x1 - x0).max(1.))
            .min((rect.height() - 80.).max(50.) / (y1 - y0).max(1.))
            .clamp(0.0001, 3.);
        self.offset = rect.size() * 0.5 - vec2((x0 + x1) * 0.5, (y0 + y1) * 0.5) * self.zoom;
    }
    fn screen(&self, world: Vec2, rect: Rect) -> Pos2 {
        rect.min + self.offset + world * self.zoom
    }
    fn world(&self, screen: Pos2, rect: Rect) -> Vec2 {
        (screen - rect.min - self.offset) / self.zoom
    }

    fn canvas(&mut self, ui: &mut egui::Ui) {
        if self.layout == Layout::Overview {
            self.overview_canvas(ui);
            return;
        }
        if self.layout == Layout::Force3d {
            self.canvas3d(ui);
            return;
        }
        let (response, painter) = ui.allocate_painter(ui.available_size(), Sense::click_and_drag());
        let rect = response.rect;
        painter.rect_filled(rect, 0., BG);
        if self.fit_next {
            self.fit(rect);
            self.fit_next = false;
        }
        if let Some(i) = self.focus_next.filter(|&i| self.node_active(i)) {
            self.focus_next = None;
            self.zoom = 1.2;
            self.offset = rect.size() * 0.5 - (self.position(i) + vec2(70., 32.)) * self.zoom;
            self.follow_layout = false;
        }
        if response.dragged() {
            self.offset += ui.input(|i| i.pointer.delta());
            self.follow_layout = false;
        }
        if response.hovered() {
            if let Some(mouse) = ui.input(|i| i.pointer.hover_pos()) {
                let wheel = ui.input(|i| i.smooth_scroll_delta.y);
                let factor = ui.input(|i| i.zoom_delta()) * (wheel * 0.002).exp();
                if factor != 1. {
                    self.follow_layout = false;
                    let anchor = self.world(mouse, rect);
                    self.zoom = (self.zoom * factor).clamp(0.0001, 4.);
                    self.offset = mouse - rect.min - anchor * self.zoom;
                }
            }
        }
        if self.zoom > 0.25 {
            let grid = 40. * self.zoom;
            let origin = rect.min + self.offset;
            let mut x = rect.left() + (origin.x - rect.left()).rem_euclid(grid);
            while x < rect.right() {
                let mut y = rect.top() + (origin.y - rect.top()).rem_euclid(grid);
                while y < rect.bottom() {
                    painter.circle_filled(pos2(x, y), 0.8, Color32::from_rgb(36, 49, 64));
                    y += grid;
                }
                x += grid;
            }
        }
        let a = self.world(rect.min, rect);
        let b = self.world(rect.max, rect);
        let visible = if self.layout == Layout::Force {
            self.graph
                .as_ref()
                .map(|g| g.visible_nodes([a.x, a.y, b.x, b.y]))
                .unwrap_or_default()
        } else {
            self.circuit.visible_nodes([a.x, a.y, b.x, b.y])
        }
        .into_iter()
        .filter(|&i| self.node_active(i))
        .collect::<Vec<_>>();
        let mut hovered = None;
        if let Some(mouse) = response.hover_pos() {
            let p = self.world(mouse, rect);
            hovered = visible.iter().rev().copied().find(|&i| {
                let n = self.position(i);
                p.x >= n.x && p.x <= n.x + 140. && p.y >= n.y && p.y <= n.y + 64.
            });
        }
        if response.clicked() {
            if let Some(i) = hovered {
                self.select(i, false);
            } else {
                self.selected = None;
                self.traced.clear();
            }
        }
        if response.double_clicked() {
            if let Some(i) = hovered {
                if self.circuit.nodes[i].kind == Kind::Input {
                    self.toggle(i);
                } else {
                    self.focus_next = Some(i);
                }
            } else {
                self.fit_next = true;
            }
        }
        self.paint_wires(&painter, rect);
        let dim = self.selected.is_some() && self.wires == Wires::Trace;
        for &i in &visible {
            let node = &self.circuit.nodes[i];
            let r = Rect::from_min_size(
                self.screen(self.position(i), rect),
                vec2(140., 64.) * self.zoom,
            );
            let selected = self.selected == Some(i);
            let muted = dim && !self.traced.contains(&i);
            let tint = if muted {
                color(node.kind).gamma_multiply(0.28)
            } else {
                color(node.kind)
            };
            if self.zoom < 0.15 {
                painter.rect_filled(r, 1., if selected { ACCENT } else { tint });
            } else {
                painter.rect_filled(
                    r,
                    5.,
                    if selected {
                        Color32::from_rgb(28, 66, 65)
                    } else if muted {
                        Color32::from_rgb(18, 26, 35)
                    } else {
                        CARD
                    },
                );
                painter.rect_stroke(
                    r,
                    5.,
                    Stroke::new(
                        if selected { 2. } else { 1. },
                        if selected {
                            ACCENT
                        } else {
                            tint.gamma_multiply(0.6)
                        },
                    ),
                    StrokeKind::Inside,
                );
                painter.rect_filled(
                    Rect::from_min_size(
                        r.min + vec2(1., 5.),
                        vec2((3. * self.zoom).max(1.), (r.height() - 10.).max(1.)),
                    ),
                    1.,
                    tint,
                );
                if self.zoom >= 0.45 {
                    let clip = painter.with_clip_rect(r.shrink(7. * self.zoom).intersect(rect));
                    let text = if muted {
                        MUTED.gamma_multiply(0.45)
                    } else {
                        TEXT
                    };
                    clip.text(
                        r.min + vec2(12., 12.) * self.zoom,
                        Align2::LEFT_TOP,
                        node.kind.name(),
                        FontId::proportional(10. * self.zoom),
                        tint,
                    );
                    clip.text(
                        r.min + vec2(12., 32.) * self.zoom,
                        Align2::LEFT_TOP,
                        &node.label,
                        FontId::proportional(13. * self.zoom),
                        text,
                    );
                }
                let on = self.circuit.states[i];
                painter.circle_filled(
                    pos2(r.right() - 10. * self.zoom, r.top() + 17. * self.zoom),
                    (3.5 * self.zoom).max(1.),
                    if on {
                        ACCENT
                    } else {
                        MUTED.gamma_multiply(0.4)
                    },
                );
                if self.zoom >= 0.3 && self.layout == Layout::Diagram {
                    for x in [r.left(), r.right()] {
                        painter.circle_filled(
                            pos2(x, r.center().y),
                            3. * self.zoom,
                            if on { ACCENT } else { MUTED },
                        );
                    }
                }
            }
        }
        if let Some(i) = hovered {
            response.clone().on_hover_ui_at_pointer(|ui| {
                let n = &self.circuit.nodes[i];
                ui.strong(&n.label);
                ui.label(format!(
                    "{} #{} · {}",
                    n.kind.name(),
                    n.id,
                    if self.circuit.states[i] { "ON" } else { "OFF" }
                ));
                if n.kind == Kind::Input {
                    ui.small("Double-click to toggle preview");
                }
            });
        }
        self.minimap(ui, rect);
        let label = format!("{:.0}%  ·  {} visible", self.zoom * 100., visible.len());
        painter.text(
            rect.left_bottom() + vec2(16., -18.),
            Align2::LEFT_BOTTOM,
            label,
            FontId::proportional(11.),
            MUTED,
        );
        if self.circuit.nodes.is_empty() {
            painter.text(
                rect.center(),
                Align2::CENTER_CENTER,
                "This circuit is empty. Open a project or blueprint to explore.",
                FontId::proportional(17.),
                MUTED,
            );
        }
    }

    fn canvas3d(&mut self, ui: &mut egui::Ui) {
        let (response, painter) = ui.allocate_painter(ui.available_size(), Sense::click_and_drag());
        let rect = response.rect;
        let size = [rect.width(), rect.height()];
        painter.rect_filled(rect, 0., BG);
        let Some(scene) = self.scene.as_ref() else {
            return;
        };
        if self.fit_next {
            self.camera.fit(&scene.positions, &scene.active);
            self.fit_next = false;
        }
        if let Some(i) = self.focus_next.filter(|&i| scene.active[i]) {
            self.camera.target = scene.positions[i];
            self.camera.distance = 650.;
            self.focus_next = None;
            self.follow_layout = false;
        }
        if response.dragged() {
            let delta = ui.input(|i| i.pointer.delta());
            let pan = response.dragged_by(egui::PointerButton::Secondary)
                || response.dragged_by(egui::PointerButton::Middle)
                || ui.input(|i| i.modifiers.shift);
            if pan {
                self.camera.pan([delta.x, delta.y], size);
            } else {
                self.camera.orbit([delta.x, delta.y]);
            }
            self.follow_layout = false;
        }
        if response.hovered() {
            let wheel = ui.input(|i| i.smooth_scroll_delta.y);
            let factor = ui.input(|i| i.zoom_delta()) * (wheel * 0.002).exp();
            if factor != 1. {
                self.camera.zoom(factor);
                self.follow_layout = false;
            }
        }
        // All positions retain their circuit indices. Screen picking traverses
        // the same depth order as painting, so the nearest visible node wins.
        let projected: Vec<_> = scene
            .positions
            .iter()
            .zip(&scene.active)
            .map(|(&p, &on)| {
                if on {
                    self.camera.project(p, size)
                } else {
                    None
                }
            })
            .collect();
        let mut visible: Vec<_> = projected
            .iter()
            .enumerate()
            .filter_map(|(i, p)| {
                let p = p.as_ref()?;
                let center = rect.min + vec2(p.screen[0], p.screen[1]);
                let radius = (22. * p.scale).clamp(2., 45.);
                if Rect::from_center_size(center, vec2(radius * 2. + 8., radius * 2. + 8.))
                    .intersects(rect)
                {
                    Some(i)
                } else {
                    None
                }
            })
            .collect();
        visible.sort_unstable_by(|&a, &b| {
            projected[b]
                .unwrap()
                .depth
                .total_cmp(&projected[a].unwrap().depth)
        });
        let hovered = response.hover_pos().and_then(|mouse| {
            visible.iter().rev().copied().find(|&i| {
                let p = projected[i].unwrap();
                let center = rect.min + vec2(p.screen[0], p.screen[1]);
                center.distance(mouse) <= (22. * p.scale).clamp(6., 45.) + 3.
            })
        });
        // Release the scene borrow before selection can update trace state.
        if response.clicked() {
            if let Some(i) = hovered {
                self.select(i, false);
            } else {
                self.selected = None;
                self.traced.clear();
            }
        }
        if response.double_clicked() {
            if let Some(i) = hovered {
                if self.circuit.nodes[i].kind == Kind::Input {
                    self.toggle(i);
                } else {
                    self.focus_next = Some(i);
                }
            } else {
                self.fit_next = true;
            }
        }
        let scene = self.scene.as_ref().unwrap();
        let fade = |depth: f32| (self.camera.distance / depth.max(1.)).clamp(0.3, 1.);
        if self.wires != Wires::Off {
            for &(a, b) in &self.circuit.edges {
                if !scene.active[a] || !scene.active[b] {
                    continue;
                }
                let selected = self.selected == Some(a) || self.selected == Some(b);
                let traced = self.traced.contains(&a) && self.traced.contains(&b);
                if self.wires == Wires::Selected && !selected
                    || self.wires == Wires::Trace && !traced
                {
                    continue;
                }
                let Some([pa, pb]) = self
                    .camera
                    .edge(scene.positions[a], scene.positions[b], size)
                else {
                    continue;
                };
                let from = rect.min + vec2(pa.screen[0], pa.screen[1]);
                let to = rect.min + vec2(pb.screen[0], pb.screen[1]);
                if !Rect::from_two_pos(from, to).expand(6.).intersects(rect) {
                    continue;
                }
                let delta = to - from;
                let distance = delta.length();
                let ra = (22. * pa.scale).clamp(2., 45.);
                let rb = (22. * pb.scale).clamp(2., 45.);
                if distance < ra + rb + 2. {
                    continue;
                }
                let direction = delta / distance;
                let from = from + direction * ra;
                let to = to - direction * rb;
                let mut stroke = self.wire_stroke(a, selected, traced);
                stroke.color = stroke
                    .color
                    .gamma_multiply(fade((pa.depth + pb.depth) * 0.5));
                painter.line_segment([from, to], stroke);
                if selected || (pa.scale + pb.scale) * 0.5 > 0.16 {
                    let side = vec2(-direction.y, direction.x);
                    let length = (8. * pb.scale).clamp(3., 9.);
                    painter
                        .line_segment([to - direction * length + side * length * 0.5, to], stroke);
                    painter
                        .line_segment([to - direction * length - side * length * 0.5, to], stroke);
                }
            }
        }
        for &i in &visible {
            let p = projected[i].unwrap();
            let center = rect.min + vec2(p.screen[0], p.screen[1]);
            let radius = (22. * p.scale).clamp(2., 45.);
            let node = &self.circuit.nodes[i];
            let selected = self.selected == Some(i);
            let dim =
                self.wires == Wires::Trace && self.selected.is_some() && !self.traced.contains(&i);
            let tint = color(node.kind).gamma_multiply(fade(p.depth) * if dim { 0.25 } else { 1. });
            painter.circle_filled(center, radius, tint);
            if radius > 4. {
                painter.circle_filled(
                    center + vec2(-radius * 0.27, -radius * 0.3),
                    radius * 0.33,
                    Color32::from_white_alpha(if dim { 10 } else { 55 }),
                );
            }
            if selected || self.circuit.states[i] {
                painter.circle_stroke(
                    center,
                    radius + 2.,
                    Stroke::new(
                        if selected { 2. } else { 1. },
                        ACCENT.gamma_multiply(if dim { 0.3 } else { 1. }),
                    ),
                );
            }
            if selected || hovered == Some(i) || (radius >= 6. && !dim && visible.len() < 350) {
                let label = if node.label.chars().count() > 34 {
                    format!("{}…", node.label.chars().take(33).collect::<String>())
                } else {
                    node.label.clone()
                };
                let at = center + vec2(0., radius + 5.);
                let galley = painter.layout_no_wrap(
                    label,
                    FontId::proportional(if selected { 14. } else { 12. }),
                    if selected {
                        ACCENT
                    } else {
                        TEXT.gamma_multiply(fade(p.depth))
                    },
                );
                let label_rect =
                    Rect::from_min_size(at - vec2(galley.size().x * 0.5, 0.), galley.size());
                painter.rect_filled(label_rect.expand(2.), 2., BG.gamma_multiply(0.9));
                painter.galley(label_rect.min, galley, TEXT);
            }
        }
        if let Some(i) = hovered {
            response.clone().on_hover_ui_at_pointer(|ui| {
                let node = &self.circuit.nodes[i];
                ui.strong(&node.label);
                ui.label(format!(
                    "{} #{} · {}",
                    node.kind.name(),
                    node.id,
                    if self.circuit.states[i] { "ON" } else { "OFF" }
                ));
                if node.kind == Kind::Input {
                    ui.small("Double-click to toggle preview");
                }
            });
        }
        self.camera_gizmo(&painter, rect);
        painter.text(
            rect.left_top() + vec2(16., 14.),
            Align2::LEFT_TOP,
            "3D FORCE GRAPH",
            FontId::proportional(12.),
            MUTED,
        );
        painter.text(
            rect.left_bottom() + vec2(120., -30.),
            Align2::LEFT_BOTTOM,
            "Drag: orbit  ·  Right / middle / Shift+drag: pan  ·  Wheel: zoom  ·  F: fit",
            FontId::proportional(11.),
            MUTED,
        );
        painter.text(
            rect.left_bottom() + vec2(120., -13.),
            Align2::LEFT_BOTTOM,
            format!(
                "{} visible · {} / {} spawned",
                visible.len(),
                scene.active_count,
                scene.positions.len()
            ),
            FontId::proportional(11.),
            MUTED,
        );
        if self.circuit.nodes.is_empty() {
            painter.text(
                rect.center(),
                Align2::CENTER_CENTER,
                "This circuit is empty. Open a project or blueprint.",
                FontId::proportional(17.),
                MUTED,
            );
        }
    }

    fn camera_gizmo(&self, painter: &egui::Painter, rect: Rect) {
        let origin = rect.left_bottom() + vec2(60., -55.);
        let axes = self.camera.axes();
        painter.circle_stroke(origin, 37., Stroke::new(1., CARD));
        let mut world_axes = [
            ("X", [1., 0., 0.], Color32::from_rgb(238, 110, 100)),
            ("Y", [0., 1., 0.], Color32::from_rgb(105, 215, 140)),
            ("Z", [0., 0., 1.], Color32::from_rgb(105, 165, 245)),
        ];
        world_axes.sort_by(|a, b| dot(a.1, axes[2]).total_cmp(&dot(b.1, axes[2])));
        for (label, axis, color) in world_axes {
            let tip = origin + vec2(dot(axis, axes[0]), -dot(axis, axes[1])) * 30.;
            painter.line_segment([origin, tip], Stroke::new(2., color));
            painter.text(
                tip,
                Align2::CENTER_CENTER,
                label,
                FontId::proportional(12.),
                color,
            );
        }
    }

    fn paint_wires(&self, painter: &egui::Painter, rect: Rect) {
        if self.wires == Wires::Off {
            return;
        }
        for &(a, b) in &self.circuit.edges {
            if !self.node_active(a) || !self.node_active(b) {
                continue;
            }
            let selected = self.selected == Some(a) || self.selected == Some(b);
            let traced = self.traced.contains(&a) && self.traced.contains(&b);
            if self.wires == Wires::Selected && !selected || self.wires == Wires::Trace && !traced {
                continue;
            }
            let source = self.position(a);
            let target = self.position(b);
            let from = self.screen(source + vec2(140., 32.), rect);
            let to = self.screen(target + vec2(0., 32.), rect);
            if self.layout == Layout::Force {
                let center_a = self.screen(source + vec2(70., 32.), rect);
                let center_b = self.screen(target + vec2(70., 32.), rect);
                let delta = center_b - center_a;
                let length = delta.length();
                if length < 0.01 {
                    continue;
                }
                let direction = delta / length;
                let border = (70. * self.zoom / direction.x.abs().max(0.0001))
                    .min(32. * self.zoom / direction.y.abs().max(0.0001));
                if length <= 2. * border {
                    continue;
                }
                let from = center_a + direction * border;
                let to = center_b - direction * border;
                if !Rect::from_two_pos(from, to).expand(4.).intersects(rect) {
                    continue;
                }
                let stroke = self.wire_stroke(a, selected, traced);
                painter.line_segment([from, to], stroke);
                if self.zoom > 0.25 {
                    let size = (7. * self.zoom).clamp(3., 16.);
                    let normal = vec2(-direction.y, direction.x);
                    painter.line_segment([to - direction * size + normal * size * 0.5, to], stroke);
                    painter.line_segment([to - direction * size - normal * size * 0.5, to], stroke);
                }
                continue;
            }
            let reach = ((to.x - from.x).abs() * 0.45).max(40. * self.zoom);
            let c1 = from + vec2(reach, 0.);
            let c2 = to - vec2(reach, 0.);
            let bounds = Rect::from_min_max(
                pos2(from.x.min(to.x).min(c2.x), from.y.min(to.y)),
                pos2(from.x.max(to.x).max(c1.x), from.y.max(to.y)),
            )
            .expand(4.);
            if !bounds.intersects(rect) {
                continue;
            }
            let stroke = self.wire_stroke(a, selected, traced);
            if self.zoom < 0.25 {
                painter.line_segment([from, to], stroke);
            } else {
                painter.add(egui::epaint::CubicBezierShape::from_points_stroke(
                    [from, c1, c2, to],
                    false,
                    Color32::TRANSPARENT,
                    stroke,
                ));
            }
            if self.zoom > 0.5 {
                let size = 5. * self.zoom;
                painter.line_segment([to - vec2(size, size * 0.65), to], stroke);
                painter.line_segment([to - vec2(size, -size * 0.65), to], stroke);
            }
        }
    }

    fn wire_stroke(&self, source: usize, selected: bool, traced: bool) -> Stroke {
        let color = if self.circuit.states[source] {
            ACCENT
        } else if selected || (self.wires == Wires::Trace && traced) {
            Color32::from_rgb(129, 158, 186)
        } else {
            Color32::from_rgb(51, 71, 91)
        };
        Stroke::new(if selected { 2. } else { 1. }, color)
    }

    fn minimap(&mut self, ui: &mut egui::Ui, canvas: Rect) {
        let map = Rect::from_min_size(
            pos2(canvas.right() - 192., canvas.bottom() - 136.),
            vec2(176., 112.),
        );
        let response = ui.interact(map, ui.id().with("minimap"), Sense::click_and_drag());
        let painter = ui.painter().with_clip_rect(map);
        painter.rect_filled(map, 5., PANEL.gamma_multiply(0.95));
        painter.rect_stroke(map, 5., Stroke::new(1., CARD), StrokeKind::Inside);
        let [x0, y0, x1, y1] = self.view_bounds();
        let scale =
            ((map.width() - 12.) / (x1 - x0).max(1.)).min((map.height() - 12.) / (y1 - y0).max(1.));
        let origin = map.center() - vec2((x0 + x1) * 0.5, (y0 + y1) * 0.5) * scale;
        let stride = (self.circuit.nodes.len() / 2000).max(1);
        for (i, n) in self.circuit.nodes.iter().enumerate().step_by(stride) {
            if !self.node_active(i) {
                continue;
            }
            painter.rect_filled(
                Rect::from_min_size(
                    origin + self.position(i) * scale,
                    vec2((140. * scale).max(1.), (64. * scale).max(1.)),
                ),
                0.,
                color(n.kind).gamma_multiply(0.65),
            );
        }
        let a = self.world(canvas.min, canvas);
        let b = self.world(canvas.max, canvas);
        painter.rect_stroke(
            Rect::from_min_max(origin + a * scale, origin + b * scale),
            0.,
            Stroke::new(1., ACCENT),
            StrokeKind::Inside,
        );
        if let Some(i) = self.selected {
            painter.circle_filled(
                origin + (self.position(i) + vec2(70., 32.)) * scale,
                2.5,
                ACCENT,
            );
        }
        if response.clicked() || response.dragged() {
            if let Some(mouse) = response.interact_pointer_pos() {
                let world = (mouse - origin) / scale;
                self.offset = canvas.size() * 0.5 - world * self.zoom;
                self.follow_layout = false;
            }
        }
        response.on_hover_text("Overview (sampled for large circuits). Click or drag to navigate.");
    }

    fn footer(&mut self, ctx: &egui::Context) {
        egui::TopBottomPanel::bottom("footer").frame(egui::Frame::new().fill(PANEL).inner_margin(10.)).show(ctx,|ui| {
            ui.horizontal(|ui| {
                ui.label(RichText::new(format!("{}  ·  TICK {}",if self.running { "RUNNING" } else if self.settling.is_some() { "SETTLING" } else { "PAUSED" },self.circuit.ticks)).small().strong().color(ACCENT));
                ui.separator(); ui.add(egui::Label::new(RichText::new(&self.status).small()).truncate()).on_hover_text(&self.status);
            });
            let controls = if self.layout == Layout::Force3d { "Drag: orbit   ·   Right / middle / Shift+drag: pan   ·   Wheel: zoom   ·   F: fit   ·   Ctrl+F: search   ·   Space: run/pause   /   Boolean preview; game timing can differ" }
                else if self.layout == Layout::Overview { "Click: inspect cluster   ·   Double-click: expand   ·   Backspace: parent   ·   Drag: pan   ·   Wheel: zoom   ·   F: fit   ·   Ctrl+F: find any gate" }
                else { "Drag: pan   ·   Wheel: zoom   ·   F: fit   ·   Ctrl+F: search   ·   Space: run/pause   ·   Double-click input: toggle   /   Boolean preview; game timing can differ" };
            ui.label(RichText::new(controls).size(10.).color(MUTED));
        });
    }

    fn keyboard(&mut self, ctx: &egui::Context) {
        if ctx.input_mut(|i| i.consume_key(egui::Modifiers::CTRL, egui::Key::O)) {
            self.open(ctx);
        }
        if ctx.input_mut(|i| i.consume_key(egui::Modifiers::CTRL, egui::Key::F)) {
            self.tab = 0;
            self.query_focus = true;
        }
        if !ctx.wants_keyboard_input() {
            if self.layout == Layout::Overview && ctx.input(|i| i.key_pressed(egui::Key::Backspace))
            {
                if let Some(parent) = self.overview.clusters[self.overview_page.scope].parent {
                    self.overview_open(parent);
                }
            }
            if ctx.input(|i| i.key_pressed(egui::Key::F)) {
                self.fit_next = true;
            }
            if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
                self.selected = None;
                self.traced.clear();
                self.running = false;
                self.settling = None;
                self.layout_task = None;
                self.overview_selected = None;
                self.overview_refresh_trace();
            }
            if !self.loading {
                if ctx.input(|i| i.key_pressed(egui::Key::Space)) {
                    self.run_toggle();
                }
                if ctx.input(|i| i.key_pressed(egui::Key::N)) {
                    self.step();
                }
                if ctx.input(|i| i.key_pressed(egui::Key::R)) {
                    self.reset();
                }
            }
        }
    }
}

impl eframe::App for Viewer {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll(ctx);
        self.keyboard(ctx);
        if let Some(path) = ctx.input(|i| i.raw.dropped_files.iter().find_map(|f| f.path.clone())) {
            self.load(path, ctx);
        }
        self.header(ctx);
        self.footer(ctx);
        self.sidebar(ctx);
        self.inspector(ctx);
        egui::CentralPanel::default()
            .frame(egui::Frame::new().fill(BG))
            .show(ctx, |ui| self.canvas(ui));
        if let Some(error) = self.error.clone() {
            let mut open = true;
            egui::Window::new("Could not complete action")
                .open(&mut open)
                .collapsible(false)
                .resizable(true)
                .show(ctx, |ui| {
                    ui.set_max_width(560.);
                    ui.label(&error);
                    if ui.button("Dismiss").clicked() {
                        self.error = None;
                    }
                });
            if !open {
                self.error = None;
            }
        }
        egui::Window::new("About the viewer").open(&mut self.show_help).collapsible(false).show(ctx,|ui| {
            ui.set_max_width(460.); ui.heading("Explore circuits without changing them");
            ui.label("Open .circuit.json, circuit.project.json or blueprint.json. Drop a file or blueprint folder anywhere in the window. The library lists saved game blueprints by name.");
            ui.separator();
            ui.label("Drag with any mouse button to pan. Wheel zooms around the pointer. F frames the circuit. Click a component or a search result to inspect it. Double-click a gate to center it; double-click blank space to fit. The overview in the corner also navigates.");
            ui.label("Selected wires show immediate connections. Trace follows Neighbors, Upstream or Downstream. All wires can be expensive on a large CPU, so large files start with Selected wires.");
            ui.label("Cluster map is selected automatically for 15,000+ blocks. It folds connectivity into bounded groups without physics. Click to inspect; double-click to expand. Up, Backspace and breadcrumbs return to a parent. Search or inspector links jump directly to a gate's leaf. Link limit caps drawn bundles, strongest first; counts include every wire. Cluster traces follow the aggregate graph in this page; gate traces use the full circuit.");
            ui.label("Choose Force-directed or 3D force to arrange components by connectivity. In 3D, drag to orbit; right/middle or Shift+drag pans; wheel zooms. Reset camera restores the viewing angle. Sphere colors identify gate types; green rings show active signals. The nearest sphere under the pointer is selected.");
            ui.label("Spawn in waves admits breadth-first batches near existing neighbors. Reveal all skips the remaining waves. Search lists pending components too: locating one reveals the graph and centers it. Freeze layout stops movement; Relax continues from the displayed arrangement and reveals remaining nodes; Restart begins the wave sequence again. Auto-fit follows the arrangement until you navigate. Diagram restores saved positions. Layout and signal simulation have independent controls.");
            ui.separator();
            ui.label("Space runs or pauses at 4 ticks/second. N steps, R resets. Settle stops on stability, a repeated state, or 512 updates. Input toggles affect only the preview. Reset restores saved initial signals.");
            ui.label("This is a parallel Boolean preview, not the game's scheduler. Native timers, buttons, sensors and other unsupported shapes and their wires are omitted and reported. Physical projection separates bodies and layers into rows. Bundled source diagrams omit physical-only export relays; turn off the library preference before opening to see physical wiring.");
            ui.label(RichText::new("The viewer has no save, edit or export operations.").color(ACCENT));
        });
    }
}

fn color(kind: Kind) -> Color32 {
    let [r, g, b] = kind.color();
    Color32::from_rgb(r, g, b)
}
