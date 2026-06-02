interface SparklineProps {
  data: number[];
  width?: number;   // 預設 80
  height?: number;  // 預設 24
  filled?: boolean;
}

export default function Sparkline({
  data,
  width = 80,
  height = 24,
  filled = false,
}: SparklineProps) {
  if (!data || data.length < 2) {
    return <svg width={width} height={height} />;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const pad = 1.5;
  const innerH = height - pad * 2;
  const innerW = width;

  const toX = (i: number) => (i / (data.length - 1)) * innerW;
  const toY = (v: number) => pad + (1 - (v - min) / range) * innerH;

  const points = data.map((v, i) => `${toX(i).toFixed(2)},${toY(v).toFixed(2)}`).join(' ');

  // Color based on first vs last value
  const isUp = data[data.length - 1] >= data[0];
  const color = isUp ? 'var(--up)' : 'var(--down)';

  const polyline = `${points}`;

  // Closed polygon for fill: add bottom-right and bottom-left corners
  const fillPolygon = [
    `${toX(0).toFixed(2)},${(height).toFixed(2)}`,
    ...data.map((v, i) => `${toX(i).toFixed(2)},${toY(v).toFixed(2)}`),
    `${toX(data.length - 1).toFixed(2)},${(height).toFixed(2)}`,
  ].join(' ');

  return (
    <svg
      width={width}
      height={height}
      style={{ display: 'block', overflow: 'visible' }}
      aria-hidden="true"
    >
      {filled && (
        <polygon
          points={fillPolygon}
          fill={color}
          opacity={0.10}
        />
      )}
      <polyline
        points={polyline}
        fill="none"
        stroke={color}
        strokeWidth={1.25}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
