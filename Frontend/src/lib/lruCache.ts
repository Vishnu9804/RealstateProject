/**
 * Minimal bounded least-recently-used cache — a plain Map (insertion order
 * doubles as recency order here: `get` re-inserts a hit, `set` always
 * inserts fresh), evicting the oldest entry once `maxEntries` is exceeded.
 *
 * Deliberately not a library: the property-detail cache (see
 * lib/propertyDetailCache.ts) is the only thing in this project that needs
 * bounded-size caching, and a ~30-line Map wrapper is easier to audit than a
 * dependency for that.
 */
export class LruCache<K, V> {
  private readonly map = new Map<K, V>();

  constructor(private readonly maxEntries: number) {}

  get(key: K): V | undefined {
    const value = this.map.get(key);
    if (value === undefined) return undefined;
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  set(key: K, value: V): void {
    this.map.delete(key);
    this.map.set(key, value);
    if (this.map.size > this.maxEntries) {
      const oldestKey = this.map.keys().next().value as K;
      this.map.delete(oldestKey);
    }
  }

  delete(key: K): void {
    this.map.delete(key);
  }
}
