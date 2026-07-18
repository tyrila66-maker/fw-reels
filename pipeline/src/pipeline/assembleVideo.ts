import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config';
import { ffmpeg, escapeForFilter } from '../utils/ffmpeg';
import { buildAss } from '../utils/ass';
import { pickGradientTheme } from '../services/localVisuals';
import { AudioSegment, SubtitlePosition, VisualSegment } from '../types';

const { videoWidth: W, videoHeight: H, fps: FPS } = config;

export interface AssembleOptions {
  subtitlePosition?: SubtitlePosition;
  /** Фоновый трек, замикшенный под озвучку (зацикливается/обрезается по длине озвучки). */
  musicPath?: string;
  /** Громкость музыки относительно озвучки, 0..1. */
  musicVolume?: number;
}

/** Растягивает/зацикливает видеофайл под точную длительность реплики и приводит к 1080x1920. */
async function renderFromVideoFile(filePath: string, durationSec: number, outPath: string): Promise<void> {
  await ffmpeg([
    '-stream_loop', '-1', // зацикливаем на случай, если исходный клип короче реплики
    '-i', filePath,
    '-t', durationSec.toFixed(3),
    '-vf', `scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H},fps=${FPS},format=yuv420p`,
    '-an',
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-crf', '20',
    outPath,
  ]);
}

/** Приводит один фоновый клип (локальная нарезка, видео, фото или градиент) к 1080x1920 и длительности реплики. */
async function normalizeSegment(
  visual: VisualSegment,
  index: number,
  durationSec: number,
  outPath: string
): Promise<void> {
  if (visual.kind === 'local-clip') {
    // Сначала вырезаем нужный участок исходника (с запасом), затем как обычное видео
    // растягиваем/зацикливаем под точную длительность реплики.
    const startSec = visual.clipStartSec ?? 0;
    const window = Math.min(durationSec + 3, 8); // запас на случай, если озвучка длиннее плана
    const trimmedPath = outPath.replace(/\.mp4$/, '.src.mp4');

    await ffmpeg([
      '-ss', startSec.toFixed(3),
      '-i', visual.filePath,
      '-t', window.toFixed(3),
      '-an',
      '-c:v', 'libx264',
      '-preset', 'veryfast',
      '-crf', '18',
      trimmedPath,
    ]);

    await renderFromVideoFile(trimmedPath, durationSec, outPath);
    await fs.unlink(trimmedPath).catch(() => {});
    return;
  }

  if (visual.kind === 'generated') {
    // Без скачивания: анимированный тёмный градиент в духе "code editor" темы.
    const { c0, c1 } = pickGradientTheme(index);
    await ffmpeg([
      '-f', 'lavfi',
      '-i',
        `gradients=size=${W}x${H}:rate=${FPS}:duration=${durationSec.toFixed(3)}:` +
        `speed=0.02:type=radial:c0=0x${c0}:c1=0x${c1}`,
      '-t', durationSec.toFixed(3),
      '-c:v', 'libx264',
      '-preset', 'veryfast',
      '-crf', '20',
      '-pix_fmt', 'yuv420p',
      outPath,
    ]);
    return;
  }

  if (visual.kind === 'video') {
    await renderFromVideoFile(visual.filePath, durationSec, outPath);
    return;
  }

  // Фото: лёгкий Ken Burns (zoompan) вместо статичной картинки.
  const frames = Math.max(1, Math.round(durationSec * FPS));
  await ffmpeg([
    '-loop', '1',
    '-i', visual.filePath,
    '-t', durationSec.toFixed(3),
    '-vf',
      `scale=${W * 2}:${H * 2}:force_original_aspect_ratio=increase,` +
      `crop=${W * 2}:${H * 2},` +
      `zoompan=z='min(zoom+0.0015,1.5)':d=${frames}:s=${W}x${H}:fps=${FPS},format=yuv420p`,
    '-an',
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-crf', '20',
    outPath,
  ]);
}

/** Шаг 4: нормализует клипы, склеивает видео+аудио(+музыку), накладывает субтитры, пишет итоговый MP4. */
export async function runAssembleStep(
  audioSegments: AudioSegment[],
  visualSegments: VisualSegment[],
  jobDir: string,
  outputPath: string,
  options: AssembleOptions = {}
): Promise<void> {
  const segmentsDir = path.join(jobDir, 'segments');
  await fs.mkdir(segmentsDir, { recursive: true });

  // 1) Приводим каждый фоновый клип к длительности своей реплики и единому формату.
  const segmentPaths: string[] = [];
  for (let i = 0; i < audioSegments.length; i++) {
    const outPath = path.join(segmentsDir, `${String(i).padStart(2, '0')}.mp4`);
    console.log(`  [video ${i + 1}/${audioSegments.length}]`);
    await normalizeSegment(visualSegments[i], i, audioSegments[i].durationSec, outPath);
    segmentPaths.push(outPath);
  }

  // 2) Склеиваем видео-сегменты (одинаковый кодек/параметры => можно "-c copy").
  const concatListPath = path.join(jobDir, 'concat.txt');
  const concatList = segmentPaths.map((p) => `file '${p.replace(/'/g, "'\\''")}'`).join('\n');
  await fs.writeFile(concatListPath, concatList, 'utf-8');

  const backgroundPath = path.join(jobDir, 'background.mp4');
  await ffmpeg(['-f', 'concat', '-safe', '0', '-i', concatListPath, '-c', 'copy', backgroundPath]);

  // 3) Склеиваем аудио-реплики в одну дорожку (concat-фильтр надёжен к разнице форматов).
  // Если ни у одной сцены нет дорожки озвучки (монтаж без голоса, filePath=''),
  // дорожку реплик не строим вообще — единственным аудио будет музыка.
  const hasNarration = audioSegments.some((segment) => segment.filePath);
  let audioPath: string | null = null;

  if (hasNarration) {
    const audioInputArgs = audioSegments.flatMap((segment) => ['-i', segment.filePath]);
    const filterInputs = audioSegments.map((_, i) => `[${i}:a]`).join('');
    audioPath = path.join(jobDir, 'audio.m4a');
    await ffmpeg([
      ...audioInputArgs,
      '-filter_complex', `${filterInputs}concat=n=${audioSegments.length}:v=0:a=1[aout]`,
      '-map', '[aout]',
      '-c:a', 'aac',
      '-b:a', '192k',
      audioPath,
    ]);
  }

  // 4) Субтитры .ass (не .srt!) с явными PlayResX/PlayResY = реальному разрешению
  // видео — иначе Alignment/MarginV в force_style у ffmpeg считаются от неверного
  // эталонного разрешения, и текст съезжает к верху кадра вместо заданной позиции.
  const assPath = path.join(jobDir, 'subtitles.ass');
  await fs.writeFile(assPath, buildAss(audioSegments, options.subtitlePosition ?? 'bottom', W, H), 'utf-8');

  const subtitleFilter = `subtitles='${escapeForFilter(assPath)}'`;

  // 5) Финальный рендер: фон + аудио (+ фоновая музыка) + вшитые субтитры -> итоговый MP4 1080x1920.
  await fs.mkdir(path.dirname(outputPath), { recursive: true });

  // Точная общая длительность — надёжнее, чем полагаться на "-shortest" при
  // зацикленной "-stream_loop -1" музыке: на Windows это сочетание не всегда
  // останавливает рендер вовремя (аудио продолжает кодироваться после конца
  // видео, пока не вылетит с загадочной "No space left on device").
  const totalDurationSec = audioSegments.reduce((sum, segment) => sum + segment.durationSec, 0);
  const durationArg = totalDurationSec.toFixed(3);

  if (options.musicPath && audioPath) {
    // Есть и озвучка, и музыка: музыка тише, чтобы не перебивать голос.
    const musicVolume = options.musicVolume ?? 0.18;
    await ffmpeg([
      '-i', backgroundPath,
      '-i', audioPath,
      '-stream_loop', '-1',
      '-i', options.musicPath,
      '-filter_complex',
        `[0:v]${subtitleFilter}[vout];` +
        `[2:a]volume=${musicVolume}[music];` +
        `[1:a][music]amix=inputs=2:duration=first:dropout_transition=0[aout]`,
      '-map', '[vout]',
      '-map', '[aout]',
      '-t', durationArg,
      '-c:v', 'libx264',
      '-preset', 'veryfast',
      '-crf', '19',
      '-c:a', 'aac',
      '-b:a', '192k',
      outputPath,
    ]);
    return;
  }

  if (options.musicPath) {
    // Без озвучки: музыка — единственная аудиодорожка, без приглушения.
    const musicVolume = options.musicVolume ?? 0.9;
    await ffmpeg([
      '-i', backgroundPath,
      '-stream_loop', '-1',
      '-i', options.musicPath,
      '-filter_complex',
        `[0:v]${subtitleFilter}[vout];` +
        `[1:a]volume=${musicVolume}[aout]`,
      '-map', '[vout]',
      '-map', '[aout]',
      '-t', durationArg,
      '-c:v', 'libx264',
      '-preset', 'veryfast',
      '-crf', '19',
      '-c:a', 'aac',
      '-b:a', '192k',
      outputPath,
    ]);
    return;
  }

  if (!audioPath) {
    throw new Error('Нет ни озвучки, ни фоновой музыки — нечего использовать как аудиодорожку.');
  }

  await ffmpeg([
    '-i', backgroundPath,
    '-i', audioPath,
    '-filter_complex', `[0:v]${subtitleFilter}[vout]`,
    '-map', '[vout]',
    '-map', '1:a',
    '-t', durationArg,
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-crf', '19',
    '-c:a', 'aac',
    '-b:a', '192k',
    outputPath,
  ]);
}
