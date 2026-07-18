import path from 'node:path';
import { config } from '../config';
import { synthesizeSpeech } from '../services/elevenlabs';
import { synthesizeSpeechLocal } from '../services/localTts';
import { getDurationSec } from '../utils/ffmpeg';
import { Script, AudioSegment } from '../types';

/**
 * Шаг 2: озвучивает каждую реплику и измеряет её длительность.
 * При наличии ELEVENLABS_API_KEY пробует ElevenLabs (mp3). Если реальный запрос
 * упал (невалидный ключ, тариф не позволяет голос и т.п.) — на первой же ошибке
 * переключается на локальный Windows TTS (wav) до конца прогона: тариф/права не
 * изменятся между репликами, поэтому повторные попытки к ElevenLabs бессмысленны.
 * Без ключа сразу используется локальный TTS.
 */
export async function runAudioStep(script: Script, jobDir: string): Promise<AudioSegment[]> {
  const audioDir = path.join(jobDir, 'audio');
  const segments: AudioSegment[] = [];
  let useElevenLabs = Boolean(config.elevenLabsApiKey);

  for (const line of script.lines) {
    const provider = useElevenLabs ? 'ElevenLabs' : 'локальный TTS (Windows)';
    console.log(
      `  [audio ${line.index + 1}/${script.lines.length}] (${line.section}, ${provider}) "${line.text}"`
    );

    let ext = useElevenLabs ? 'mp3' : 'wav';
    let filePath = path.join(audioDir, `${String(line.index).padStart(2, '0')}.${ext}`);

    if (useElevenLabs) {
      try {
        await synthesizeSpeech(line.text, filePath);
      } catch (error) {
        console.warn(
          `  [audio] ElevenLabs недоступен (${(error as Error).message}) — ` +
            'дальше переключаюсь на локальный TTS (Windows) без ключей.'
        );
        useElevenLabs = false;
        ext = 'wav';
        filePath = path.join(audioDir, `${String(line.index).padStart(2, '0')}.${ext}`);
        await synthesizeSpeechLocal(line.text, filePath);
      }
    } else {
      await synthesizeSpeechLocal(line.text, filePath);
    }

    const durationSec = await getDurationSec(filePath);
    segments.push({ line, filePath, durationSec });
  }

  return segments;
}
