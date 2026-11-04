//! Perspective orbit camera, independent of the UI and layout worker.
use crate::physics::{Point, dot, length, sub};

#[derive(Clone, Debug)]
pub struct Camera {
    pub target: Point,
    pub distance: f32,
    pub yaw: f32,
    pub pitch: f32,
}
#[derive(Clone, Copy, Debug)]
pub struct Projected {
    pub screen: [f32; 2],
    pub depth: f32,
    pub scale: f32,
}
impl Default for Camera {
    fn default() -> Self {
        Self {
            target: [0.; 3],
            distance: 1400.,
            yaw: 0.5,
            pitch: -0.35,
        }
    }
}
impl Camera {
    pub fn axes(&self) -> [Point; 3] {
        let (s, c) = self.yaw.sin_cos();
        let (sp, cp) = self.pitch.sin_cos();
        [[c, 0., -s], [s * sp, cp, c * sp], [s * cp, -sp, c * cp]]
    }
    pub fn depth(&self, p: Point) -> f32 {
        self.distance - dot(sub(p, self.target), self.axes()[2])
    }
    pub fn project(&self, p: Point, size: [f32; 2]) -> Option<Projected> {
        let delta = sub(p, self.target);
        let [right, up, toward] = self.axes();
        let depth = self.distance - dot(delta, toward);
        if depth < 10. || !depth.is_finite() {
            return None;
        }
        let scale = size[0].min(size[1]) * 0.9 / depth;
        Some(Projected {
            screen: [
                size[0] * 0.5 + dot(delta, right) * scale,
                size[1] * 0.5 - dot(delta, up) * scale,
            ],
            depth,
            scale,
        })
    }
    pub fn edge(&self, mut a: Point, mut b: Point, size: [f32; 2]) -> Option<[Projected; 2]> {
        let da = self.depth(a);
        let db = self.depth(b);
        let near = 10.1;
        if da < near && db < near {
            return None;
        }
        if da < near {
            let t = (near - da) / (db - da);
            a = std::array::from_fn(|i| a[i] + (b[i] - a[i]) * t);
        } else if db < near {
            let t = (near - db) / (da - db);
            b = std::array::from_fn(|i| b[i] + (a[i] - b[i]) * t);
        }
        Some([self.project(a, size)?, self.project(b, size)?])
    }
    pub fn fit(&mut self, points: &[Point], active: &[bool]) {
        let mut low = [f32::INFINITY; 3];
        let mut high = [f32::NEG_INFINITY; 3];
        let mut any = false;
        for (p, &on) in points.iter().zip(active) {
            if on {
                any = true;
                for i in 0..3 {
                    low[i] = low[i].min(p[i]);
                    high[i] = high[i].max(p[i]);
                }
            }
        }
        if !any {
            self.target = [0.; 3];
            self.distance = 700.;
            return;
        }
        self.target = std::array::from_fn(|i| (low[i] + high[i]) * 0.5);
        let radius = points
            .iter()
            .zip(active)
            .filter(|&(_, on)| *on)
            .map(|(&p, _)| length(sub(p, self.target)))
            .fold(100., f32::max);
        self.distance = (radius * 2.3 + 100.).max(500.);
    }
    pub fn orbit(&mut self, delta: [f32; 2]) {
        self.yaw -= delta[0] * 0.008;
        self.pitch = (self.pitch - delta[1] * 0.008).clamp(-1.5, 1.5);
    }
    pub fn pan(&mut self, delta: [f32; 2], size: [f32; 2]) {
        let [right, up, _] = self.axes();
        let scale = self.distance / (size[0].min(size[1]) * 0.9).max(1.);
        for i in 0..3 {
            self.target[i] += (-delta[0] * right[i] + delta[1] * up[i]) * scale;
        }
    }
    pub fn zoom(&mut self, factor: f32) {
        self.distance = (self.distance / factor).clamp(60., 1e8);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn perspective_orbit_and_clipping() {
        let mut c = Camera {
            yaw: 0.,
            pitch: 0.,
            ..Default::default()
        };
        let center = c.project([0.; 3], [800., 600.]).unwrap();
        assert_eq!(center.screen, [400., 300.]);
        assert!(c.project([100., 0., 500.], [800., 600.]).unwrap().scale > center.scale);
        assert!(c.project([0., 0., 2000.], [800., 600.]).is_none());
        let e = c.edge([0., 0., 0.], [0., 0., 2000.], [800., 600.]).unwrap();
        assert!(e.iter().all(|p| p.depth >= 10.));
        c.orbit([150., 30.]);
        assert_ne!(
            center.screen,
            c.project([100., 0., 0.], [800., 600.]).unwrap().screen
        );
    }
    #[test]
    fn fit_handles_empty_and_single_nodes_and_pan_moves_target() {
        let mut c = Camera::default();
        c.fit(&[], &[]);
        assert!(c.distance.is_finite());
        c.fit(&[[100., 200., 300.]], &[true]);
        assert_eq!(c.target, [100., 200., 300.]);
        c.pan([20., -10.], [800., 600.]);
        assert_ne!(c.target, [100., 200., 300.]);
        c.zoom(1e20);
        assert_eq!(c.distance, 60.);
    }

    #[test]
    fn fit_frames_active_points_and_ignores_pending_outliers() {
        let points = [
            [-600., 0., 0.],
            [600., 0., 0.],
            [0., 600., 600.],
            [0., -600., -600.],
            [1e8, 1e8, 1e8],
        ];
        let mut c = Camera::default();
        c.fit(&points, &[true, true, true, true, false]);
        assert!(c.distance < 10_000.);
        for size in [[800., 600.], [400., 900.]] {
            for &point in &points[..4] {
                let p = c.project(point, size).unwrap();
                assert!(
                    p.screen[0] > 0.
                        && p.screen[0] < size[0]
                        && p.screen[1] > 0.
                        && p.screen[1] < size[1]
                );
            }
        }
    }
}
