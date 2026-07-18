import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config';
import { generateScript as callClaude } from '../services/anthropic';
import { Script } from '../types';

/**
 * Шаг 1: сценарий из 8 реплик (хук, проблема, решение, призыв к действию).
 * При наличии ANTHROPIC_API_KEY — пишет сценарий Claude. Без ключа берёт готовый
 * сценарий из файла (--script <path>), т.к. без API-ключа вызвать модель нельзя.
 */
export async function runScriptStep(
  topic: string,
  jobDir: string,
  scriptFilePath?: string
): Promise<Script> {
  let script: Script;

  if (config.anthropicApiKey) {
    script = await callClaude(topic);
  } else if (scriptFilePath) {
    const raw = await fs.readFile(scriptFilePath, 'utf-8');
    script = JSON.parse(raw) as Script;
    // Ровно 8 реплик (хук/проблема/решение/CTA) — требование конкретно этого формата
    // сценариев, а не пайплайна в целом: сценарии-монтажи (section: "scene") могут
    // содержать сколько угодно сцен.
    if (!script.lines || script.lines.length === 0) {
      throw new Error(`Файл сценария "${scriptFilePath}" не содержит ни одной реплики`);
    }
  } else {
    throw new Error(
      'Нет ANTHROPIC_API_KEY и не передан --script <path>. ' +
        'Задайте ключ в .env либо укажите готовый JSON-сценарий флагом --script.'
    );
  }

  await fs.writeFile(path.join(jobDir, 'script.json'), JSON.stringify(script, null, 2), 'utf-8');
  return script;
}
