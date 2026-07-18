/**
 * Фон без ключей и без скачивания — набор тёмных "code editor" тем для ffmpeg-фильтра gradients.
 * Каждой реплике достаётся своя пара цветов (по кругу), чтобы фон менялся между сегментами.
 */
export interface GradientTheme {
  c0: string;
  c1: string;
}

const THEMES: GradientTheme[] = [
  { c0: '0d1117', c1: '161b22' }, // GitHub dark
  { c0: '1e1e2e', c1: '313244' }, // Dracula-ish
  { c0: '0f172a', c1: '1e293b' }, // Slate dark
  { c0: '1a1b26', c1: '2f334d' }, // Tokyo Night
];

export function pickGradientTheme(index: number): GradientTheme {
  return THEMES[index % THEMES.length];
}
