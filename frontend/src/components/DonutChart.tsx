import { useEffect, useRef } from 'react';

interface DonutChartProps {
  labels: string[];
  values: number[];
  colors: string[];
  height?: number;
}

export default function DonutChart({ labels, values, colors, height = 260 }: DonutChartProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || labels.length === 0) return;
    let cancelled = false;

    import('plotly.js-dist-min').then((Plotly: any) => {
      if (cancelled || !ref.current) return;
      Plotly.newPlot(ref.current, [{
        type: 'pie',
        hole: 0.55,
        labels,
        values,
        marker: { colors },
        textinfo: 'percent',
        hovertemplate: '%{label}: %{value:.2f} (%{percent})<extra></extra>',
        textfont: { size: 11, color: '#94a3b8' },
      }], {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        margin: { t: 0, b: 0, l: 0, r: 0 },
        height,
        showlegend: true,
        legend: {
          font: { color: '#94a3b8', size: 10 },
          bgcolor: 'transparent',
          x: 1, y: 0.5,
        },
      }, { displayModeBar: false, responsive: true });
    });

    return () => { cancelled = true; };
  }, [labels, values, colors, height]);

  return <div ref={ref} style={{ width: '100%', height }} />;
}
