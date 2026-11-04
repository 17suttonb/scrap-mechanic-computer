use super::*;

impl Viewer {
    pub(super) fn overview_open(&mut self, scope: usize) {
        self.overview_page = self.overview.page(scope, &self.circuit);
        self.overview_selected = None;
        self.overview_focus_slot = None;
        self.fit_next = true;
        self.overview_refresh_trace();
    }

    pub(super) fn overview_refresh_trace(&mut self) {
        self.overview_marks = vec![false; self.overview_page.items.len()];
        self.overview_trace_links = vec![0; self.overview_page.bundles.len()];
        if self.selected.is_some() {
            for &i in &self.traced {
                let slot = self.overview_page.mapping[i];
                if slot != NONE {
                    self.overview_marks[slot] = true;
                }
            }
            for (i, bundle) in self.overview_page.bundles.iter().enumerate() {
                self.overview_trace_links[i] = bundle
                    .endpoints
                    .iter()
                    .filter(|&&(a, b)| self.traced.contains(&a) && self.traced.contains(&b))
                    .count();
            }
        } else if let Some(slot) = self.overview_selected {
            self.overview_marks = self.overview_page.trace_items(
                slot,
                match self.trace {
                    Trace::Neighbors => None,
                    Trace::Upstream => Some(true),
                    Trace::Downstream => Some(false),
                },
            );
            for (i, b) in self.overview_page.bundles.iter().enumerate() {
                if self.overview_marks[b.from] && self.overview_marks[b.to] {
                    self.overview_trace_links[i] = b.endpoints.len();
                }
            }
        }
    }

    fn overview_choose(&mut self, slot: usize) {
        match self.overview_page.items[slot] {
            Item::Cluster(_) => {
                self.selected = None;
                self.traced.clear();
                self.overview_selected = Some(slot);
                self.overview_refresh_trace();
            }
            Item::Node(i) => {
                self.select(i, false);
                self.overview_selected = Some(slot);
            }
        }
    }

    fn overview_selection(&self) -> Option<usize> {
        self.overview_selected.or_else(|| {
            self.selected.and_then(|i| {
                let slot = self.overview_page.mapping[i];
                (slot != NONE).then_some(slot)
            })
        })
    }

    fn overview_name(&self, slot: usize) -> &str {
        match self.overview_page.items[slot] {
            Item::Cluster(c) => &self.overview.clusters[c].name,
            Item::Node(i) => &self.circuit.nodes[i].label,
        }
    }

    pub(super) fn overview_controls(&mut self, ctx: &egui::Context) {
        egui::TopBottomPanel::top("overview_controls")
            .frame(egui::Frame::new().fill(PANEL).inner_margin(8.))
            .show(ctx, |ui| {
                let scope = self.overview_page.scope;
                let mut path = vec![scope];
                while let Some(parent) = self.overview.clusters[*path.last().unwrap()].parent {
                    path.push(parent);
                }
                path.reverse();
                let mut open = None;
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new("CLUSTER MAP").small().color(ACCENT));
                    if let Some(parent) = self.overview.clusters[scope].parent {
                        if ui.button("Up").clicked() {
                            open = Some(parent);
                        }
                    }
                    for id in path {
                        if ui
                            .selectable_label(id == scope, &self.overview.clusters[id].name)
                            .clicked()
                        {
                            open = Some(id);
                        }
                        if id != scope {
                            ui.label("/");
                        }
                    }
                    ui.separator();
                    ui.label(
                        RichText::new(format!(
                            "{} blocks · {} tiles",
                            self.overview.clusters[scope].members.len(),
                            self.overview_page.items.len()
                        ))
                        .small()
                        .color(MUTED),
                    );
                    ui.label("Link limit");
                    ui.add(
                        egui::DragValue::new(&mut self.overview_link_budget)
                            .range(8..=256)
                            .speed(2),
                    );
                    ui.label(
                        RichText::new("Click to inspect · double-click to expand")
                            .small()
                            .color(MUTED),
                    );
                });
                if let Some(scope) = open {
                    self.overview_open(scope);
                }
            });
    }

    pub(super) fn overview_inspector(&mut self, ctx: &egui::Context) {
        let slot = self.overview_selected.unwrap();
        let Item::Cluster(id) = self.overview_page.items[slot] else {
            return;
        };
        let (mut open, mut choose, mut neighbor) = (false, None, None);
        egui::SidePanel::right("inspector")
            .default_width(248.)
            .width_range(220. ..=400.)
            .show(ctx, |ui| {
                let cluster = &self.overview.clusters[id];
                ui.add_space(14.);
                ui.label(RichText::new("CLUSTER INSPECTOR").small().color(MUTED));
                ui.heading(&cluster.name);
                ui.label(&cluster.sample);
                ui.label(
                    RichText::new("Automatic connectivity group")
                        .small()
                        .color(MUTED),
                );
                egui::Grid::new("cluster_stats").show(ui, |ui| {
                    for (key, value) in [
                        ("Blocks", cluster.members.len()),
                        ("Preview ON", self.overview_counts[id]),
                        ("Internal wires", cluster.internal),
                        ("Incoming wires", cluster.incoming),
                        ("Outgoing wires", cluster.outgoing),
                        ("Inputs", cluster.inputs),
                        ("Outputs", cluster.outputs),
                    ] {
                        ui.label(key);
                        ui.label(value.to_string());
                        ui.end_row();
                    }
                });
                if ui.button("Open cluster").clicked() {
                    open = true;
                }
                ui.collapsing("Connections at this level", |ui| {
                    for (title, incoming) in [("INCOMING", true), ("OUTGOING", false)] {
                        ui.label(RichText::new(title).small().color(MUTED));
                        let connections: Vec<_> = self
                            .overview_page
                            .bundles
                            .iter()
                            .filter(|b| {
                                if incoming {
                                    b.to == slot
                                } else {
                                    b.from == slot
                                }
                            })
                            .collect();
                        for b in connections.iter().take(12) {
                            let j = if incoming { b.from } else { b.to };
                            if ui
                                .button(format!(
                                    "{} · {} wires",
                                    self.overview_name(j),
                                    b.endpoints.len()
                                ))
                                .clicked()
                            {
                                neighbor = Some(j);
                            }
                        }
                        if connections.len() > 12 {
                            ui.small(format!("{} more connections", connections.len() - 12));
                        }
                    }
                    ui.small("Use Up to follow connections outside this page.");
                });
                ui.separator();
                ui.label(
                    RichText::new("COMPONENTS · click to locate")
                        .small()
                        .color(MUTED),
                );
                egui::ScrollArea::vertical()
                    .id_salt("cluster_members")
                    .show_rows(ui, 25., cluster.members.len(), |ui, rows| {
                        for row in rows {
                            let i = cluster.members[row];
                            let n = &self.circuit.nodes[i];
                            if ui
                                .add_sized(
                                    [ui.available_width(), 25.],
                                    egui::Button::new(format!("{} #{}", n.label, n.id))
                                        .truncate()
                                        .fill(CARD),
                                )
                                .on_hover_text(&n.label)
                                .clicked()
                            {
                                choose = Some(i);
                            }
                        }
                    });
            });
        if open {
            self.overview_open(id);
        } else if let Some(slot) = neighbor {
            self.overview_choose(slot);
            self.overview_focus_slot = Some(slot);
        }
        if let Some(i) = choose {
            self.select(i, true);
        }
    }

    pub(super) fn overview_canvas(&mut self, ui: &mut egui::Ui) {
        let (response, painter) = ui.allocate_painter(ui.available_size(), Sense::click_and_drag());
        let rect = response.rect;
        painter.rect_filled(rect, 0., BG);
        if let Some(i) = self.focus_next.take() {
            let leaf = self.overview.node_leaf[i];
            if self.overview_page.scope != leaf {
                self.overview_open(leaf);
            }
            self.overview_selected = Some(self.overview_page.mapping[i]);
            self.overview_focus_slot = self.overview_selected;
            self.overview_refresh_trace();
            ui.ctx().request_repaint();
        }
        if self.fit_next {
            let [x0, y0, x1, y1] = self.overview_page.bounds;
            self.zoom = ((rect.width() - 72.).max(50.) / (x1 - x0).max(1.))
                .min((rect.height() - 100.).max(50.) / (y1 - y0).max(1.))
                .clamp(0.02, 2.);
            self.offset = rect.size() * 0.5 - vec2((x0 + x1) * 0.5, (y0 + y1) * 0.5) * self.zoom;
            self.fit_next = false;
        }
        if let Some(slot) = self.overview_focus_slot.take() {
            let p = self.overview_page.positions[slot];
            self.zoom = 1.1;
            self.offset = rect.size() * 0.5 - vec2(p[0] + 110., p[1] + 56.) * self.zoom;
        }
        if response.dragged() {
            self.offset += ui.input(|i| i.pointer.delta());
        }
        if response.hovered() {
            if let Some(mouse) = response.hover_pos() {
                let factor = ui.input(|i| i.zoom_delta())
                    * (ui.input(|i| i.smooth_scroll_delta.y) * 0.002).exp();
                if factor != 1. {
                    let anchor = self.world(mouse, rect);
                    self.zoom = (self.zoom * factor).clamp(0.02, 3.);
                    self.offset = mouse - rect.min - anchor * self.zoom;
                }
            }
        }
        let tiles: Vec<_> = self
            .overview_page
            .positions
            .iter()
            .map(|p| {
                Rect::from_min_size(
                    self.screen(vec2(p[0], p[1]), rect),
                    vec2(220., 112.) * self.zoom,
                )
            })
            .collect();
        let hovered = response.hover_pos().and_then(|mouse| {
            tiles
                .iter()
                .enumerate()
                .rev()
                .find(|(_, r)| r.contains(mouse))
                .map(|(i, _)| i)
        });
        if response.clicked() {
            if let Some(slot) = hovered {
                self.overview_choose(slot);
            } else {
                self.overview_selected = None;
                self.selected = None;
                self.traced.clear();
                self.overview_refresh_trace();
            }
        }
        if response.double_clicked() {
            if let Some(slot) = hovered {
                match self.overview_page.items[slot] {
                    Item::Cluster(c) => {
                        self.overview_open(c);
                        ui.ctx().request_repaint();
                        return;
                    }
                    Item::Node(i) => {
                        if self.circuit.nodes[i].kind == Kind::Input {
                            self.toggle(i);
                        } else {
                            self.overview_focus_slot = Some(slot);
                        }
                    }
                }
            } else {
                self.fit_next = true;
            }
        }
        let selected = self.overview_selection();
        let mut eligible = 0;
        let mut drawn = 0;
        let mut hovered_link = None;
        if self.wires != Wires::Off {
            for (index, b) in self.overview_page.bundles.iter().enumerate() {
                let connected = selected == Some(b.from) || selected == Some(b.to);
                if self.wires == Wires::Selected && !connected
                    || self.wires == Wires::Trace && self.overview_trace_links[index] == 0
                {
                    continue;
                }
                eligible += 1;
                if drawn >= self.overview_link_budget {
                    continue;
                }
                let a = tiles[b.from].center();
                let z = tiles[b.to].center();
                let delta = z - a;
                let len = delta.length();
                if len < 0.1 {
                    continue;
                }
                let d = delta / len;
                let cut = (110. * self.zoom / d.x.abs().max(0.001))
                    .min(56. * self.zoom / d.y.abs().max(0.001));
                let normal = vec2(-d.y, d.x);
                let offset = normal
                    * if b.reciprocal {
                        (4. * self.zoom).max(2.5)
                    } else {
                        0.
                    };
                let from = a + d * cut + offset;
                let to = z - d * cut + offset;
                if !Rect::from_two_pos(from, to).expand(5.).intersects(rect) {
                    continue;
                }
                drawn += 1;
                let hit = hovered.is_none()
                    && response
                        .hover_pos()
                        .is_some_and(|p| segment_distance(p, from, to) < 5.);
                if hit {
                    hovered_link = Some(index);
                }
                let color = if hit {
                    TEXT
                } else if b.active > 0 {
                    ACCENT
                } else if connected {
                    MUTED
                } else {
                    Color32::from_rgb(62, 86, 109)
                };
                let width = (1. + (b.endpoints.len() as f32).ln_1p() * 0.35).min(4.);
                let stroke = Stroke::new(width, color);
                painter.line_segment([from, to], stroke);
                let side = vec2(-d.y, d.x);
                let arrow = (7. * self.zoom).clamp(3., 10.);
                painter.line_segment([to - d * arrow + side * arrow * 0.5, to], stroke);
                painter.line_segment([to - d * arrow - side * arrow * 0.5, to], stroke);
                if connected || hit {
                    let label = if self.wires == Wires::Trace
                        && self.overview_trace_links[index] != b.endpoints.len()
                    {
                        format!("{}/{}", self.overview_trace_links[index], b.endpoints.len())
                    } else {
                        b.endpoints.len().to_string()
                    };
                    let galley = painter.layout_no_wrap(label, FontId::proportional(10.), color);
                    let r = Rect::from_center_size(
                        from + (to - from) * 0.5 + normal * if b.reciprocal { 8. } else { 0. },
                        galley.size() + vec2(8., 4.),
                    );
                    painter.rect_filled(r, 3., BG);
                    painter.galley(r.min + vec2(4., 2.), galley, color);
                }
            }
        }
        for (slot, &item) in self.overview_page.items.iter().enumerate() {
            let r = tiles[slot];
            if !r.intersects(rect) {
                continue;
            }
            let (name, sample, total, on, tint) = match item {
                Item::Cluster(c) => {
                    let cluster = &self.overview.clusters[c];
                    (
                        cluster.name.clone(),
                        cluster.sample.clone(),
                        cluster.members.len(),
                        self.overview_counts[c],
                        color(cluster.dominant),
                    )
                }
                Item::Node(i) => {
                    let n = &self.circuit.nodes[i];
                    (
                        n.label.clone(),
                        format!("{} · #{}", n.kind.name(), n.id),
                        1,
                        usize::from(self.circuit.states[i]),
                        color(n.kind),
                    )
                }
            };
            let selected = selected == Some(slot);
            let dim = self.wires == Wires::Trace
                && (self.selected.is_some() || self.overview_selected.is_some())
                && !self.overview_marks[slot];
            let fill = if selected {
                Color32::from_rgb(28, 66, 65)
            } else if dim {
                BG
            } else if hovered == Some(slot) {
                Color32::from_rgb(38, 54, 73)
            } else {
                CARD
            };
            painter.rect_filled(r, 6., fill);
            painter.rect_stroke(
                r,
                6.,
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
            let clip = painter.with_clip_rect(r.shrink(8. * self.zoom).intersect(rect));
            let left = r.min + vec2(12., 11.) * self.zoom;
            let text = if dim { MUTED.gamma_multiply(0.5) } else { TEXT };
            clip.text(
                left,
                Align2::LEFT_TOP,
                name,
                FontId::proportional((15. * self.zoom).clamp(8., 22.)),
                text,
            );
            clip.text(
                left + vec2(0., 26.) * self.zoom,
                Align2::LEFT_TOP,
                sample,
                FontId::proportional((11. * self.zoom).clamp(8., 17.)),
                MUTED,
            );
            let stats = match item {
                Item::Cluster(_) => format!(
                    "{total} blocks · {} internal wires",
                    self.overview_page.folded[slot]
                ),
                Item::Node(i) => format!(
                    "{} · {} incoming · {} outgoing",
                    if on > 0 { "ON" } else { "OFF" },
                    self.circuit.incoming[i].len(),
                    self.circuit.outgoing[i].len()
                ),
            };
            clip.text(
                left + vec2(0., 54.) * self.zoom,
                Align2::LEFT_TOP,
                stats,
                FontId::proportional((11. * self.zoom).clamp(8., 17.)),
                text,
            );
            let bar = Rect::from_min_size(
                r.min + vec2(12., 87.) * self.zoom,
                vec2(196., 6.) * self.zoom,
            );
            painter.rect_filled(bar, 2., BG);
            if total > 0 && on > 0 {
                painter.rect_filled(
                    Rect::from_min_size(
                        bar.min,
                        vec2(bar.width() * on as f32 / total as f32, bar.height()),
                    ),
                    2.,
                    ACCENT,
                );
            }
            clip.text(
                pos2(r.right() - 12. * self.zoom, r.bottom() - 8. * self.zoom),
                Align2::RIGHT_BOTTOM,
                format!("{on} ON"),
                FontId::proportional((9. * self.zoom).clamp(8., 14.)),
                if on > 0 { ACCENT } else { MUTED },
            );
        }
        if let Some(slot) = hovered {
            response.clone().on_hover_ui_at_pointer(|ui| {
                ui.strong(self.overview_name(slot));
                match self.overview_page.items[slot] {
                    Item::Cluster(c) => {
                        let cluster = &self.overview.clusters[c];
                        ui.label(&cluster.sample);
                        ui.label(format!(
                            "{} blocks · {} internal wires",
                            cluster.members.len(),
                            cluster.internal
                        ));
                        ui.label(format!(
                            "{} incoming / {} outgoing wires",
                            cluster.incoming, cluster.outgoing
                        ));
                        ui.small("Click to inspect. Double-click to expand.");
                    }
                    Item::Node(i) => {
                        ui.label(format!(
                            "{} #{}",
                            self.circuit.nodes[i].kind.name(),
                            self.circuit.nodes[i].id
                        ));
                    }
                }
            });
        } else if let Some(i) = hovered_link {
            let b = &self.overview_page.bundles[i];
            response.clone().on_hover_ui_at_pointer(|ui| {
                ui.strong(format!(
                    "{} → {}",
                    self.overview_name(b.from),
                    self.overview_name(b.to)
                ));
                ui.label(format!(
                    "{} wires · {} carrying ON",
                    b.endpoints.len(),
                    b.active
                ));
            });
        }
        let external = format!(
            "{} internal wires · {} entering / {} leaving this page",
            self.overview_page.internal, self.overview_page.incoming, self.overview_page.outgoing
        );
        painter.rect_filled(
            Rect::from_min_size(rect.left_bottom() - vec2(0., 42.), vec2(rect.width(), 42.)),
            0.,
            BG,
        );
        painter.text(
            rect.left_bottom() + vec2(14., -27.),
            Align2::LEFT_BOTTOM,
            external,
            FontId::proportional(10.),
            MUTED,
        );
        let hint = if self.wires == Wires::Selected && selected.is_none() {
            "Select a tile to show its connections".into()
        } else {
            format!(
                "{drawn} / {eligible} eligible links shown · strongest first · link limit {}",
                self.overview_link_budget
            )
        };
        painter.text(
            rect.left_bottom() + vec2(14., -10.),
            Align2::LEFT_BOTTOM,
            hint,
            FontId::proportional(10.),
            MUTED,
        );
        if self.overview_page.items.is_empty() {
            painter.text(
                rect.center(),
                Align2::CENTER_CENTER,
                "This circuit is empty. Open a project or blueprint.",
                FontId::proportional(17.),
                MUTED,
            );
        }
    }
}

fn segment_distance(p: Pos2, a: Pos2, b: Pos2) -> f32 {
    let ab = b - a;
    let t = ((p - a).dot(ab) / ab.length_sq().max(0.001)).clamp(0., 1.);
    p.distance(a + ab * t)
}
