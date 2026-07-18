import { AudioSegment, SubtitlePosition } from '../types';

function formatAssTimestamp(seconds: number): string {
  const centiseconds = Math.round(seconds * 100);
  const h = Math.floor(centiseconds / 360000);
  const m = Math.floor((centiseconds % 360000) / 6000);
  const s = Math.floor((centiseconds % 6000) / 100);
  const cs = centiseconds % 100;
  const pad = (n: number, len = 2) => n.toString().padStart(len, '0');
  return `${h}:${pad(m)}:${pad(s)}.${pad(cs)}`;
}

function escapeAssText(text: string): string {
  return text.replace(/\n/g, '\\N').replace(/\{/g, '\\{').replace(/\}/g, '\\}');
}

/** Лёгкий pop-in (небольшой overshoot по масштабу) + fade — как в трендовых Reels-титрах. */
const POP_IN_TAG =
  '{\\fscx82\\fscy82\\t(0,180,\\fscx103\\fscy103)\\t(180,260,\\fscx100\\fscy100)\\fad(180,150)}';

/**
 * Строит .ass с явными PlayResX/PlayResY, равными реальному разрешению видео.
 * Без этого `subtitles=file.srt` + force_style у ffmpeg считает Alignment/MarginV
 * от неверного эталонного разрешения — текст съезжает к верху кадра вместо центра
 * (проверено: тот же force_style на .srt даёт сдвиг вверх, на .ass с PlayRes — точный центр).
 *
 * Стиль подобран по результатам разбора реальных трендовых Reels (не по наитию):
 * жирный гротеск без подложки-плашки, белый текст с тонкой обводкой — так выглядит
 * большинство живых примеров подписей в Instagram. Сцены с пустым text (b-roll без
 * подписи) не создают событие субтитров, но их длительность всё равно учитывается
 * в общем таймлайне.
 */
export function buildAss(
  segments: AudioSegment[],
  position: SubtitlePosition,
  videoWidth: number,
  videoHeight: number
): string {
  const alignment = position === 'center' ? 5 : 2;
  const marginV = position === 'center' ? 0 : Math.round(videoHeight * 0.1);
  const fontSize = 78;

  const header = `[Script Info]
ScriptType: v4.00+
PlayResX: ${videoWidth}
PlayResY: ${videoHeight}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI Black,${fontSize},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,1,${alignment},60,60,${marginV},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text`;

  let cursor = 0;
  const events: string[] = [];

  for (const segment of segments) {
    const start = cursor;
    const end = cursor + segment.durationSec;
    cursor = end;

    if (segment.line.text.trim()) {
      const text = POP_IN_TAG + escapeAssText(segment.line.text);
      events.push(`Dialogue: 0,${formatAssTimestamp(start)},${formatAssTimestamp(end)},Default,,0,0,0,,${text}`);
    }
  }

  return `${header}\n${events.join('\n')}\n`;
}
