import fs from 'node:fs';
import path from 'node:path';
import axios from 'axios';
import { config } from '../config';
import { ensureDir } from '../utils/files';
import { describeAxiosError } from '../utils/http';

/** Озвучивает текст через ElevenLabs TTS и сохраняет mp3 в destPath. */
export async function synthesizeSpeech(text: string, destPath: string): Promise<void> {
  const url = `https://api.elevenlabs.io/v1/text-to-speech/${config.elevenLabsVoiceId}`;

  let response;
  try {
    response = await axios.post(
      url,
      {
        text,
        model_id: config.elevenLabsModelId,
        voice_settings: {
          stability: 0.45,
          similarity_boost: 0.8,
        },
      },
      {
        headers: {
          'xi-api-key': config.elevenLabsApiKey,
          'Content-Type': 'application/json',
          Accept: 'audio/mpeg',
        },
        responseType: 'arraybuffer',
      }
    );
  } catch (error) {
    // Никогда не пробрасываем "сырую" ошибку axios дальше — в её config.headers
    // лежит xi-api-key, а необработанный throw дошёл бы до console.error(err) в index.ts.
    throw new Error(`ElevenLabs: ${describeAxiosError(error)}`);
  }

  await ensureDir(path.dirname(destPath));
  await fs.promises.writeFile(destPath, response.data);
}
