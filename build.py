"""Build and verify the current accelerated RV32E release from source."""
from pathlib import Path
import argparse,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
if sys.platform=='win32' and sys.version_info[:2]==(3,13) and sys.maxsize>2**32:
    sys.path.insert(0,str(ROOT/'vendor'))
from accelerated.build import build_current
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'out-blueprints')
    parser.add_argument('--work',type=Path,default=ROOT/'.build')
    parser.add_argument('--flat',action='store_true',help='lay the logic blocks out as a single upright wall instead of a cube (unpinned variant)')
    parser.add_argument('--beta',action='store_true',help='build an unpinned beta: optimised firmware and netlist passes, release.json hash checks skipped (cube layout unless --flat)')
    parser.add_argument('--fast-shifts',action=argparse.BooleanOptionalAction,default=None,help='single-cycle fixed shifts (default on with --beta, otherwise off; verified in the simulator only)')
    parser.add_argument('--key-queue',type=int,default=None,help='keystroke FIFO depth on the console (default 16 with --beta, otherwise 0 = original single-key tap)')
    args=parser.parse_args()
    if args.fast_shifts is None:args.fast_shifts=args.beta
    if args.key_queue is None:args.key_queue=16 if args.beta else 0
    if args.beta and args.output==ROOT/'out-blueprints':args.output=ROOT/'out-blueprints-beta'
    if args.flat and args.output==ROOT/'out-blueprints':args.output=ROOT/'out-blueprints-flat'
    result,work=build_current(ROOT,args.output,args.work,flat=args.flat,beta=args.beta,fast_shifts=args.fast_shifts,key_queue=args.key_queue)
    print('Verification record:',work/'verification.json')
if __name__=='__main__':main()
