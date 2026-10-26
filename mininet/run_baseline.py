"""Compatibility entry point for the controlled concurrent experiment runner."""
import sys
from run_detailed_experiment import main

if __name__ == "__main__":
    sys.argv.insert(1, "baseline")
    main()
