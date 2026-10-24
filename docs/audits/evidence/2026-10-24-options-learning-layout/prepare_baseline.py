"""Prepare the read-only pre-change sources for render_fixture.cjs before."""
from pathlib import Path
import subprocess
baseline = "1a8d4c1e9ded790a8badd2d8225d3eef613474b7"
root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
for name in ("src/lib/optionsFlowStatus.ts", "src/lib/optionsTabCharts.ts", "src/components/OptionsTabCharts.tsx", "src/components/MarketPressurePanel.tsx", "src/styles/globals.css"):
    target = Path("/tmp/options-layout-baseline") / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(["git", "show", f"{baseline}:frontend/{name}"], cwd=root))
