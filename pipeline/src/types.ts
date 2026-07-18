export type ScriptSection = 'hook' | 'problem' | 'solution' | 'cta' | 'scene';

export type SubtitlePosition = 'bottom' | 'center';

export interface ScriptLine {
  index: number;
  section: ScriptSection;
  text: string;
  visualPrompt: string;
  /** Если задано — сцена берёт видео из локального файла (input/<sourceClip>), а не из Pexels/градиента. */
  sourceClip?: string;
  /** Секунда начала нарезки внутри sourceClip. */
  clipStartSec?: number;
  /** Длительность сцены в секундах для монтажа без озвучки (см. generateDurations.ts). */
  durationSec?: number;
}

export interface Script {
  topic: string;
  language: string;
  lines: ScriptLine[];
}

export interface AudioSegment {
  line: ScriptLine;
  filePath: string;
  durationSec: number;
}

export interface VisualSegment {
  line: ScriptLine;
  filePath: string;
  kind: 'video' | 'image' | 'generated' | 'local-clip';
  clipStartSec?: number;
}
