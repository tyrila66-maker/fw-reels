import path from 'node:path';
import { config } from './config';
import { ensureDir, slugify } from './utils/files';
import { runScriptStep } from './pipeline/generateScript';
import { runAudioStep } from './pipeline/generateAudio';
import { runDurationsStep } from './pipeline/generateDurations';
import { runVisualsStep } from './pipeline/generateVisuals';
import { runAssembleStep } from './pipeline/assembleVideo';
import { SubtitlePosition } from './types';

interface ParsedArgs {
  topic: string;
  scriptFilePath?: string;
  musicPath?: string;
  subtitlePosition: SubtitlePosition;
  outputOverride?: string;
  noVoiceover: boolean;
}

function extractFlag(args: string[], flag: string): string | undefined {
  const idx = args.indexOf(flag);
  if (idx === -1) return undefined;
  const value = args[idx + 1];
  args.splice(idx, 2);
  return value;
}

function extractBooleanFlag(args: string[], flag: string): boolean {
  const idx = args.indexOf(flag);
  if (idx === -1) return false;
  args.splice(idx, 1);
  return true;
}

function parseArgs(argv: string[]): ParsedArgs {
  const args = [...argv];

  const scriptFilePath = extractFlag(args, '--script');
  const musicPath = extractFlag(args, '--music');
  const outputOverride = extractFlag(args, '--out');
  const subtitlePositionRaw = extractFlag(args, '--subtitle-position');
  const noVoiceover = extractBooleanFlag(args, '--no-voiceover');

  const subtitlePosition: SubtitlePosition = subtitlePositionRaw === 'center' ? 'center' : 'bottom';

  return { topic: args.join(' ').trim(), scriptFilePath, musicPath, subtitlePosition, outputOverride, noVoiceover };
}

async function main(): Promise<void> {
  const { topic, scriptFilePath, musicPath, subtitlePosition, outputOverride, noVoiceover } = parseArgs(
    process.argv.slice(2)
  );
  if (!topic) {
    console.error(
      'Использование: npm run generate -- "<тема ролика>" ' +
        '[--script <path/to/script.json>] [--music <path/to/track>] ' +
        '[--subtitle-position bottom|center] [--out <path/to/output.mp4>] [--no-voiceover]'
    );
    process.exit(1);
  }

  const slug = slugify(topic);
  const jobDir = path.join(config.tmpDir, `${Date.now()}-${slug}`);
  const outputPath = outputOverride
    ? path.resolve(process.cwd(), outputOverride)
    : path.join(config.outputDir, `${slug}.mp4`);

  await ensureDir(jobDir);

  const scriptSource = config.anthropicApiKey ? 'Claude' : `готовый файл (${scriptFilePath})`;
  console.log(`[1/4] Сценарий по теме: "${topic}" (источник: ${scriptSource})`);
  const script = await runScriptStep(topic, jobDir, scriptFilePath);

  let audioSegments;
  if (noVoiceover) {
    console.log('[2/4] Тайминги сцен (без озвучки — монтаж под музыку)');
    audioSegments = await runDurationsStep(script);
  } else {
    const audioSource = config.elevenLabsApiKey
      ? 'ElevenLabs, при ошибке — локальный TTS (Windows)'
      : 'локальный TTS (Windows, без ключей)';
    console.log(`[2/4] Озвучка реплик (${audioSource})`);
    audioSegments = await runAudioStep(script, jobDir);
  }

  const hasLocalClips = script.lines.some((line) => line.sourceClip);
  const visualSource = hasLocalClips
    ? 'локальные нарезки исходников + Pexels'
    : config.pexelsApiKey
      ? 'Pexels'
      : 'генерируемый градиент (без ключей)';
  console.log(`[3/4] Фоновые видео/фото (${visualSource})`);
  const visualSegments = await runVisualsStep(script, jobDir);

  console.log(
    `[4/4] Сборка видео (ffmpeg; субтитры: ${subtitlePosition}` +
      `${musicPath ? `; музыка: ${musicPath}` : ''}${noVoiceover ? '; без озвучки' : ''})`
  );
  await runAssembleStep(audioSegments, visualSegments, jobDir, outputPath, {
    subtitlePosition,
    musicPath,
  });

  console.log(`\nГотово: ${outputPath}`);
}

main().catch((err) => {
  // Печатаем только message/stack, а не сырой объект: у ошибок HTTP-клиентов
  // там могут быть заголовки запроса (в т.ч. API-ключи).
  console.error(err instanceof Error ? err.stack ?? err.message : err);
  process.exit(1);
});
