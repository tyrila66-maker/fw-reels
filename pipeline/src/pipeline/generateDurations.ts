import { Script, AudioSegment } from '../types';

const DEFAULT_SCENE_DURATION_SEC = 1.5;

/**
 * Шаг 2 (режим без озвучки): вместо TTS берёт длительность каждой сцены из
 * line.durationSec (монтаж под музыку, а не под реплики). filePath = '' —
 * признак "нет дорожки озвучки для этой сцены" для assembleVideo.ts.
 */
export async function runDurationsStep(script: Script): Promise<AudioSegment[]> {
  return script.lines.map((line) => {
    const durationSec = line.durationSec ?? DEFAULT_SCENE_DURATION_SEC;
    const caption = line.text ? `"${line.text}"` : '(без подписи)';
    console.log(`  [scene ${line.index + 1}/${script.lines.length}] (${line.section}, ${durationSec}s) ${caption}`);
    return { line, filePath: '', durationSec };
  });
}
