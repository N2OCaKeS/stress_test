/**
 * SHA256-fingerprint публичного SSH-ключа в каноничном для OpenSSH виде
 * `SHA256:<base64-без-паддинга>`.
 *
 * OpenSSH считает отпечаток как base64(sha256(<сырой key-blob>)), где blob —
 * это вторая колонка строки ключа (`ssh-ed25519 <blob> comment`), декодированная
 * из base64 в байты. Хеш берётся от этих байт, а не от текста строки.
 *
 * Считаем через `crypto.subtle.digest` — он асинхронный, поэтому функция
 * возвращает Promise. Если строка ключа не парсится (пустая, без blob или с
 * битым base64) — возвращаем null, чтобы вызывающий показал «—».
 */
export async function sshKeyFingerprint(
  publicKey: string | null | undefined,
): Promise<string | null> {
  if (!publicKey) return null;
  const parts = publicKey.trim().split(/\s+/);
  // Формат: "<type> <base64blob> [comment]". Blob — обязательная вторая колонка;
  // некоторые экспортируют ключ без типа, тогда blob идёт первым.
  const blob = parts.length >= 2 ? parts[1] : parts[0];
  if (!blob) return null;

  let buf: ArrayBuffer;
  try {
    const bin = atob(blob);
    if (bin.length === 0) return null;
    buf = new ArrayBuffer(bin.length);
    const raw = new Uint8Array(buf);
    for (let i = 0; i < bin.length; i++) raw[i] = bin.charCodeAt(i);
  } catch {
    return null;
  }

  const digest = await crypto.subtle.digest("SHA-256", buf);
  const bytes = new Uint8Array(digest);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  const b64 = btoa(bin).replace(/=+$/, "");
  return `SHA256:${b64}`;
}
