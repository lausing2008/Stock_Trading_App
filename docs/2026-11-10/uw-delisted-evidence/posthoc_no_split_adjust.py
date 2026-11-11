"""POST-HOC (after uw-results.txt was read): the planned split rule mistook real crashes for
splits (First Republic 2023-03-13 and -16, FMC 2025-02-05, SolarEdge 2023-10-20). Re-run the same
study with no split adjustment at all, to see whether the conclusion depends on it."""
import sys
import uw_study as U
U.SPLITS = ()
U.main(*sys.argv[1:7])
