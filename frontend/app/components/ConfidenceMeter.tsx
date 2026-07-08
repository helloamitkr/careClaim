export function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.75
      ? "bg-teal-500"
      : value >= 0.5
        ? "bg-amber-500"
        : "bg-red-500";

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 rounded-full bg-black/10 dark:bg-white/10 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-black/60 dark:text-white/60">{pct}%</span>
    </div>
  );
}
