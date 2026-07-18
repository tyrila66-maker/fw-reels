import path from 'node:path';
import { config } from '../config';
import { downloadBackground } from '../services/pexels';
import { Script, VisualSegment } from '../types';

/**
 * Шаг 3: фон для каждой реплики.
 * Приоритет источников для каждой сцены:
 *  1) line.sourceClip задан — берём конкретную нарезку из локального файла (input/<sourceClip>);
 *  2) есть PEXELS_API_KEY — скачивает видео/фото по visualPrompt;
 *  3) иначе — генерируемый ffmpeg-градиент без ключей и скачивания (см. assembleVideo.ts).
 */
export async function runVisualsStep(script: Script, jobDir: string): Promise<VisualSegment[]> {
  const visualsDir = path.join(jobDir, 'visuals');
  const segments: VisualSegment[] = [];
  const usePexels = Boolean(config.pexelsApiKey);

  for (const line of script.lines) {
    if (line.sourceClip) {
      const filePath = path.join(config.inputDir, line.sourceClip);
      console.log(
        `  [visual ${line.index + 1}/${script.lines.length}] (${line.section}, локальная нарезка) ` +
          `${line.sourceClip} @ ${line.clipStartSec ?? 0}s — "${line.visualPrompt}"`
      );
      segments.push({ line, filePath, kind: 'local-clip', clipStartSec: line.clipStartSec ?? 0 });
      continue;
    }

    if (!usePexels) {
      console.log(
        `  [visual ${line.index + 1}/${script.lines.length}] (${line.section}, генерируемый фон) "${line.visualPrompt}"`
      );
      segments.push({ line, filePath: '', kind: 'generated' });
      continue;
    }

    const baseName = String(line.index).padStart(2, '0');
    console.log(
      `  [visual ${line.index + 1}/${script.lines.length}] (${line.section}, Pexels) "${line.visualPrompt}"`
    );
    const { filePath, kind } = await downloadBackground(line.visualPrompt, visualsDir, baseName);
    segments.push({ line, filePath, kind });
  }

  return segments;
}
