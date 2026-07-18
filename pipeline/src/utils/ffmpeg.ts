import { spawn } from 'node:child_process';
import ffmpegStatic from 'ffmpeg-static';
import ffprobeStatic from 'ffprobe-static';

const FFMPEG_BIN = ffmpegStatic as unknown as string;
const FFPROBE_BIN = (ffprobeStatic as unknown as { path: string }).path;

function run(bin: string, label: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn(bin, args);
    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    proc.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`${label} завершился с кодом ${code}:\n${stderr.slice(-2000)}`));
    });
  });
}

export function ffmpeg(args: string[]): Promise<string> {
  return run(FFMPEG_BIN, 'ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', ...args]);
}

export async function getDurationSec(filePath: string): Promise<number> {
  const out = await run(FFPROBE_BIN, 'ffprobe', [
    '-v', 'error',
    '-show_entries', 'format=duration',
    '-of', 'default=noprint_wrappers=1:nokey=1',
    filePath,
  ]);
  const value = parseFloat(out.trim());
  if (Number.isNaN(value)) {
    throw new Error(`Не удалось прочитать длительность файла: ${filePath}`);
  }
  return value;
}

/** Экранирование пути для ffmpeg filtergraph (subtitles=..., двоеточия дисков Windows и т.п.) */
export function escapeForFilter(filePath: string): string {
  return filePath.replace(/\\/g, '/').replace(/:/g, '\\:');
}
