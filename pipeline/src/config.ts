import 'dotenv/config';
import path from 'node:path';

// Все ключи необязательны: без них соответствующий шаг переключается на локальный
// no-key фоллбэк (см. localTts.ts / localVisuals.ts и --script у index.ts).
export const config = {
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || '',

  elevenLabsApiKey: process.env.ELEVENLABS_API_KEY || '',
  elevenLabsVoiceId: process.env.ELEVENLABS_VOICE_ID || '21m00Tcm4TlvDq8ikWAM',
  elevenLabsModelId: process.env.ELEVENLABS_MODEL_ID || 'eleven_multilingual_v2',

  pexelsApiKey: process.env.PEXELS_API_KEY || '',

  outputDir: path.resolve(process.cwd(), 'output'),
  tmpDir: path.resolve(process.cwd(), 'tmp'),
  // Локальные исходники для сцен с явной нарезкой (sourceClip в ScriptLine).
  inputDir: path.resolve(process.cwd(), 'input'),

  videoWidth: 1080,
  videoHeight: 1920,
  fps: 30,
};
