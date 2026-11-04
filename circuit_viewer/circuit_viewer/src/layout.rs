//! Planar viewport snapshot derived from active force-layout nodes.

#[derive(Clone, Debug)]
pub struct GraphView {
    /// Top-left corners of the viewer's 140 Ã— 64 component cards.
    pub positions: Vec<[f32; 2]>,
    pub bounds: [f32; 4],
}

impl GraphView {
    pub fn from_active(centers: &[[f32; 3]], active: &[bool]) -> Self {
        let flat: Vec<_> = centers.iter().map(|p| [p[0], p[1]]).collect();
        let mut view = Self::from_centers(&flat);
        let mut bounds = [
            f32::INFINITY,
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::NEG_INFINITY,
        ];
        let mut any = false;
        for (p, &on) in view.positions.iter().zip(active) {
            if on {
                any = true;
                bounds[0] = bounds[0].min(p[0]);
                bounds[1] = bounds[1].min(p[1]);
                bounds[2] = bounds[2].max(p[0] + 140.);
                bounds[3] = bounds[3].max(p[1] + 64.);
            }
        }
        view.bounds = if any { bounds } else { [0., 0., 400., 300.] };
        view
    }
    pub fn from_centers(centers: &[[f32; 2]]) -> Self {
        let mut bounds = [
            f32::INFINITY,
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::NEG_INFINITY,
        ];
        let positions = centers
            .iter()
            .map(|&[x, y]| {
                let p = [x - 70., y - 32.];
                bounds[0] = bounds[0].min(p[0]);
                bounds[1] = bounds[1].min(p[1]);
                bounds[2] = bounds[2].max(p[0] + 140.);
                bounds[3] = bounds[3].max(p[1] + 64.);
                p
            })
            .collect();
        if centers.is_empty() {
            bounds = [0., 0., 400., 300.];
        }
        Self { positions, bounds }
    }

    pub fn visible_nodes(&self, rect: [f32; 4]) -> Vec<usize> {
        self.positions
            .iter()
            .enumerate()
            .filter(|(_, p)| {
                p[0] + 140. >= rect[0]
                    && p[1] + 64. >= rect[1]
                    && p[0] <= rect[2]
                    && p[1] <= rect[3]
            })
            .map(|(i, _)| i)
            .collect()
    }

    pub fn centers(&self) -> Vec<[f32; 2]> {
        self.positions
            .iter()
            .map(|p| [p[0] + 70., p[1] + 32.])
            .collect()
    }
}
