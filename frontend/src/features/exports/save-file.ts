/**
 * Hand bytes to the browser as a file download. Kept in its own module so
 * tests can replace it (jsdom has no downloads or object URLs).
 */
export function saveFile(bytes: ArrayBuffer, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  // Revoked later: some browsers read the URL after click() returns.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
