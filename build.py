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
    args=parser.parse_args()
    if args.flat and args.output==ROOT/'out-blueprints':args.output=ROOT/'out-blueprints-flat'
    result,work=build_current(ROOT,args.output,args.work,flat=args.flat)
    print('Verification record:',work/'verification.json')
if __name__=='__main__':main()
