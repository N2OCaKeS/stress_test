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
