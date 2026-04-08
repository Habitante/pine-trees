import sys
from pine_trees.agent import run, run_genesis

if __name__ == "__main__":
    if "--genesis" in sys.argv:
        idx = sys.argv.index("--genesis")
        n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 5
        run_genesis(n)
    else:
        run()
