/**
 * Railway's published usage rates (docs.railway.com/reference/pricing/plans):
 *   RAM  $10 / GB / month   = $0.000231 / GB / minute
 *   CPU  $20 / vCPU / month = $0.000463 / vCPU / minute
 * billed on what the service actually uses, per second. GB is taken as 10^9
 * bytes, which errs on the side of a slightly higher estimate.
 *
 * Plans: the free Trial is a one-time $5 credit (30 days, up to 1 GB RAM and
 * 2 vCPU per service); Hobby is $5 / month and includes $5 of usage.
 */
export const RAM_USD_PER_GB_SECOND = 0.000231 / 60;
export const CPU_USD_PER_VCPU_SECOND = 0.000463 / 60;
export const SECONDS_PER_MONTH = 30 * 24 * 3600;
export const INCLUDED_USAGE_USD = 5;
export const TRIAL_RAM_BYTES = 1e9;

const BYTES_PER_GB = 1e9;

/** Cost of holding `byteSeconds` of memory (bytes × seconds held). */
export function ramCost(byteSeconds: number): number {
  return (byteSeconds / BYTES_PER_GB) * RAM_USD_PER_GB_SECOND;
}

/** Cost of `cpuSeconds` of CPU time. */
export function cpuCost(cpuSeconds: number): number {
  return cpuSeconds * CPU_USD_PER_VCPU_SECOND;
}

export function formatUsd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "$0";
  if (value >= 100) return `$${value.toFixed(0)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  if (value >= 0.01) return `$${value.toFixed(3)}`;
  if (value >= 0.0001) return `$${value.toFixed(5)}`;
  return "<$0.0001";
}
