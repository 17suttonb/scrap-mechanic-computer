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
    args=parser.parse_args();result,work=build_current(ROOT,args.output,args.work)
    print('Verification record:',work/'verification.json')
if __name__=='__main__':main()
