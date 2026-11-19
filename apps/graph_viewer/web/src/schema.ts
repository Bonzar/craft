export interface Schema<T> { parse(value: unknown): T }
type Shape = Record<string, Schema<unknown>>;
type Parsed<T extends Shape> = { [K in keyof T]: T[K] extends Schema<infer U> ? U : never };
function invalid(): never { throw new Error('Ответ источника не соответствует формату. Сцена сохранена без изменений.'); }
export const str: Schema<string> = { parse: value => typeof value === 'string' ? value : invalid() };
export const num: Schema<number> = { parse: value => typeof value === 'number' && Number.isFinite(value) ? value : invalid() };
export const uint: Schema<number> = { parse: value => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : invalid() };
export const bool: Schema<boolean> = { parse: value => typeof value === 'boolean' ? value : invalid() };
export const unknownValue: Schema<unknown> = { parse: value => value };
export function array<T>(schema: Schema<T>): Schema<T[]> { return { parse(value) { return Array.isArray(value) ? value.map(item => schema.parse(item)) : invalid(); } }; }
export function nullable<T>(schema: Schema<T>): Schema<T | null> { return { parse: value => value === null ? null : schema.parse(value) }; }
export function optional<T>(schema: Schema<T>, fallback: T): Schema<T> { return { parse: value => value === undefined ? fallback : schema.parse(value) }; }
export function oneOf<const T extends readonly string[]>(values: T): Schema<T[number]> { return { parse: value => typeof value === 'string' && values.includes(value) ? value : invalid() }; }
export function object<T extends Shape>(shape: T): Schema<Parsed<T>> {
  return { parse(value) {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return invalid();
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(shape)) { const parser = shape[key]; if (parser) result[key] = parser.parse(Reflect.get(value, key)); }
    return result as Parsed<T>;
  } };
}
export const vector: Schema<[number, number, number]> = { parse(value) {
  const parsed = array(num).parse(value);
  const [x, y, z] = parsed;
  if (parsed.length !== 3 || x === undefined || y === undefined || z === undefined) return invalid();
  return [x, y, z];
} };
export type Infer<T> = T extends Schema<infer U> ? U : never;

export const sourceKey: Schema<string | number> = { parse: value => typeof value === 'string' ? value : uint.parse(value) };
