export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z]/g, '');
}
