"""Build and verify the current accelerated RV32E release from source."""
from pathlib import Path
import argparse,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
if sys.platform=='win32' and sys.version_info[:2]==(3,13) and sys.maxsize>2**32:
    sys.path.insert(0,str(ROOT/'vendor'))
from accelerated.build import build_current
BETA_CPU_TICKS=[16,2,8,2]
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'out-blueprints')
    parser.add_argument('--work',type=Path,default=ROOT/'.build')
    parser.add_argument('--flat',action='store_true',help='same as --layout flat')
    parser.add_argument('--layout',choices=('cube','flat','districts'),default=None,help='logic block placement: cube, one upright wall, or named districts by function (default districts with --beta, otherwise cube; anything but cube on the pinned path is an unpinned variant)')
    parser.add_argument('--beta',action='store_true',help='build an unpinned beta: optimised firmware and netlist passes, release.json hash checks skipped (districts layout unless --layout says otherwise)')
    parser.add_argument('--fast-shifts',action=argparse.BooleanOptionalAction,default=None,help='single-cycle fixed shifts (default on with --beta, otherwise off; verified in the simulator only)')
    parser.add_argument('--key-queue',type=int,default=None,help='keystroke FIFO depth on the console (default 16 with --beta, otherwise 0 = original single-key tap)')
    parser.add_argument('--cartridge',type=Path,default=None,help='cartridge file (from cartridge/make_cartridge.py), or a bare RV32E binary run as a single program in board mode, built into a third CARTRIDGE module (default cartridge/programs.bin with --beta; needs --beta)')
    parser.add_argument('--no-cartridge',action='store_true',help='build --beta without the cartridge module: the menu says so')
    parser.add_argument('--cpu-ticks',default=None,help='four CPU timer settings, capture,gap,commit,gap (default 16,2,8,2 with --beta; the pinned clock is 20,6,12,6)')
    parser.add_argument('--screen-rows',type=int,default=None,help='text rows on the screen, up to 8 (default 8 with --beta, otherwise 4)')
    args=parser.parse_args()
    if args.flat:
        if args.layout not in (None,'flat'):parser.error('--flat contradicts --layout '+args.layout)
        args.layout='flat'
    if args.layout is None:args.layout='districts' if args.beta else 'cube'
    args.flat=args.layout=='flat'
    if args.fast_shifts is None:args.fast_shifts=args.beta
    if args.key_queue is None:args.key_queue=16 if args.beta else 0
    if args.beta and args.output==ROOT/'out-blueprints':args.output=ROOT/'out-blueprints-beta'
    if args.flat and args.output==ROOT/'out-blueprints':args.output=ROOT/'out-blueprints-flat'
    if args.layout=='districts' and args.output==ROOT/'out-blueprints':args.output=ROOT/'out-blueprints-districts'
    if args.cartridge is None and args.beta and not args.no_cartridge:args.cartridge=ROOT/'cartridge'/'programs.bin'
    if args.no_cartridge:args.cartridge=None
    result,work=build_current(ROOT,args.output,args.work,flat=args.flat,districts=args.layout=='districts',beta=args.beta,fast_shifts=args.fast_shifts,key_queue=args.key_queue,cartridge=args.cartridge,cpu_ticks=[int(v) for v in args.cpu_ticks.split(',')] if args.cpu_ticks else (BETA_CPU_TICKS if args.beta else None),screen_rows=args.screen_rows or (8 if args.beta else 4))
    print('Verification record:',work/'verification.json')
if __name__=='__main__':main()
