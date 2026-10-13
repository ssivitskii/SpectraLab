/** Display-only min/max envelope: preserves narrow peaks and their original x values. */
export function displaySeries(
  x: number[],
  y: number[],
  maxPoints = 4000,
): { x: number[]; y: number[] } {
  if (x.length <= maxPoints) return { x, y };
  const output = new Set<number>([0, x.length - 1]);
  const bucket = Math.ceil((x.length - 2) / Math.floor((maxPoints - 2) / 2));
  for (let start = 1; start < x.length - 1; start += bucket) {
    let low = start,
      high = start;
    for (
      let index = start + 1;
      index < Math.min(start + bucket, x.length - 1);
      index++
    ) {
      if (y[index] < y[low]) low = index;
      if (y[index] > y[high]) high = index;
    }
    output.add(low);
    output.add(high);
  }
  const indexes = [...output].sort((a, b) => a - b);
  return { x: indexes.map((i) => x[i]), y: indexes.map((i) => y[i]) };
}
