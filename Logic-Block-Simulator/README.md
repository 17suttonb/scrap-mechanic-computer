# Logic Block Simulator

Portable Windows x64 bundle of the Scrap Mechanic native logic and timer
simulator, model `native-logic-2`. Python is included. No installation,
additional packages, internet connection, or game installation is needed.

Extract the entire ZIP to a writable folder, then double-click **Run Demo.bat**.
The included 8x32-bit timer RAM completes its 30-operation self-test and prints:

```text
Timer RAM self-test: PASS
  READY: 1
  DONE: 1
  PASSED: 1
  FAILED: 0
  PROGRESS: 30
```

The exact signal trace and resumable state are written into `results`.
**Run Tests.bat** runs the 18 core tests, including randomized comparisons
against the independent full-tick reference implementation.

## Simulate your own blueprint

This is a command-line simulator. Open PowerShell in the extracted folder:

```powershell
.\Simulator.bat 'C:\path\to\export-folder' --ticks 12000 --inputs 'C:\path\to\inputs.json' --watch ready --watch done --output result.json --snapshot-out state.json
```

The export folder must contain `blueprint.json`. Named ports additionally use
`port-map.json`. Use `--watch '#123'` for a controller ID without a port map.
`clock.run` / `clock.reset` and `service.name` read the corresponding clock maps;
`key.A` reads `terminal-map.json`. Copy those maps alongside your blueprint when
using those signal names. The bundled example needs only its port map.

Example input script:

```json
[
  {"tick": 0, "set": {"reset": 1}},
  {"tick": 480, "set": {"reset": 0}}
]
```

Each input request commits at tick+1. `--ticks` means additional logical updates.
Resume with `--snapshot-in state.json`; any new input requests must be at or
after the snapshot tick. `--record record.json` can replace the positional folder
if the record's `folder` points to an existing local export.

For the bundled example:

```powershell
.\Simulator.bat examples\timer-ram --ticks 12000 --inputs examples\timer-ram\inputs.json --watch ready --watch done --watch passed --watch failed --watch progress --output results\custom.json
```

Compare captured states at explicit game update numbers:

```powershell
.\'Compare Trace.bat' 'C:\path\to\export-folder' capture.json --output comparison.json
```

The capture format is:

```json
{
  "source": "Describe how these controller states were captured",
  "inputs": [{"commit_tick": 2, "signals": {"#1": 1}}],
  "samples": [{"tick": 3, "signals": {"#2": 1}}]
}
```

## Model and source

All six vanilla gates, switches, buttons and timers use the inspected
Scrap Mechanic 1.0.6.889 controller path. Each gate computes from the preceding
published outputs. Timers preserve their full 2,432-bit history. Snapshots
include timer histories and pending input requests. Strict import rejects
unsupported wired controllers, duplicate connections, reciprocal connections
without relays, and excess connections.

The model does not reproduce rendering, physics, networking or UI delivery.
Initial states come from JSON with empty timer histories unless a snapshot is
provided. Nominal seconds use 40 updates/second; this does not measure game
wall-clock performance. The exact game cold-import sequence remains unresolved.

The simulator, independent reference implementations, core tests and their
local source dependencies are included. The simulator lives in
`hdl_workshop/native_simulator.py`; `native_sim_cli.py` supplies the CLI.
`MANIFEST.json` records the SHA-256 of every bundled file except itself.
The included Python license is `runtime/LICENSE.txt`.
