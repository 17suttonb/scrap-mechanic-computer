#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod viewer;
use circuit_viewer::model::Circuit;
use std::path::PathBuf;

fn main() -> eframe::Result {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let physical = args.iter().any(|a| a == "--physical");
    let paths: Vec<PathBuf> = args
        .iter()
        .filter(|a| *a != "--physical" && *a != "--check")
        .map(PathBuf::from)
        .collect();
    // Headless validation also exercises the real parser against large generated files.
    if args.iter().any(|a| a == "--check") {
        if paths.is_empty() {
            eprintln!("Usage: circuit-viewer --check [--physical] <file-or-folder> ...");
            std::process::exit(2);
        }
        let mut failed = false;
        for path in paths {
            match Circuit::load(&path, !physical) {
                Ok(c) => println!(
                    "{}: {} blocks, {} wires — {}",
                    c.name,
                    c.nodes.len(),
                    c.edges.len(),
                    c.report
                ),
                Err(error) => {
                    eprintln!("{}: {error}", path.display());
                    failed = true;
                }
            }
        }
        std::process::exit(if failed { 1 } else { 0 });
    }
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_inner_size([1480., 940.])
            .with_min_inner_size([1000., 680.])
            .with_drag_and_drop(true),
        ..Default::default()
    };
    eframe::run_native(
        "Circuit Workshop · Viewer",
        options,
        Box::new(move |cc| {
            Ok(Box::new(viewer::Viewer::new(
                cc,
                paths.into_iter().next(),
                !physical,
            )))
        }),
    )
}
