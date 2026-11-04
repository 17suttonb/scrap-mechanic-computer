from accelerated.exact_sharing import share
from accelerated.exact_constants import fold

def compact(build):
    return share(fold(share(build), inductive_cycles=True))
