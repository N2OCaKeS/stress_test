/**
 * UTF-8-safe base64 для write-полей с секретами (`password_b64`).
 *
 * Голый `btoa` бросает на не-ASCII символах, поэтому строку сначала режем в
 * байты через `TextEncoder`, а потом уже кодируем. Бэкенд симметрично делает
 * base64-decode и получает исходный plaintext в UTF-8.
 */
export function toBase64(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

/**
 * Парный к `toBase64` UTF-8-safe декодер для reveal-показа секретов.
 *
 * Голый `atob` отдаёт строку из byte-значений, и не-ASCII (кириллица, emoji)
 * рассыпается в мусор. Поэтому сначала собираем байты по char-кодам, а потом
 * собираем обратно UTF-8 через `TextDecoder`.
 */
export function fromBase64(b64: string): string {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
