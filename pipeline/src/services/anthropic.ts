import Anthropic from '@anthropic-ai/sdk';
import { config } from '../config';
import { Script, ScriptLine, ScriptSection } from '../types';

const client = new Anthropic({ apiKey: config.anthropicApiKey });

const SECTIONS: ScriptSection[] = ['hook', 'problem', 'solution', 'cta'];

const SUBMIT_SCRIPT_TOOL: Anthropic.Tool = {
  name: 'submit_script',
  description: 'Отправить готовый сценарий Reels ровно из 8 реплик.',
  strict: true,
  input_schema: {
    type: 'object',
    properties: {
      language: {
        type: 'string',
        description: 'Язык, на котором написан сценарий, например "russian".',
      },
      lines: {
        type: 'array',
        minItems: 8,
        maxItems: 8,
        items: {
          type: 'object',
          properties: {
            section: { type: 'string', enum: SECTIONS },
            text: {
              type: 'string',
              description: 'Одна произносимая реплика, 8-16 слов, без ремарок и пояснений.',
            },
            visual_prompt: {
              type: 'string',
              description:
                'Короткий запрос на английском для поиска фонового видео/фото под эту реплику ' +
                '(например: "tired office worker at night, moody blue light").',
            },
          },
          required: ['section', 'text', 'visual_prompt'],
          additionalProperties: false,
        },
      },
    },
    required: ['language', 'lines'],
    additionalProperties: false,
  },
} as Anthropic.Tool;

const SYSTEM_PROMPT = [
  'Ты пишешь сценарии для коротких вертикальных видео (Reels/TikTok/Shorts).',
  'Сценарий всегда состоит ровно из 8 произносимых реплик в таком порядке:',
  '1 реплика-хук, 2-3 реплики про проблему, 3-4 реплики про решение, 1 реплика-призыв к действию (CTA).',
  'Пиши на том же языке, на котором дана тема пользователя.',
  'Каждая реплика — короткая и произносится за 2-4 секунды (примерно 8-16 слов), без markdown и ремарок.',
  'Для каждой реплики придумай visual_prompt — короткое описание фонового видео/фото НА АНГЛИЙСКОМ ',
  'языке, подходящее по смыслу к этой реплике (для поиска стоковых роликов).',
  'Вызови инструмент submit_script ровно один раз с готовым сценарием.',
].join(' ');

export async function generateScript(topic: string): Promise<Script> {
  const response = await client.messages.create({
    model: 'claude-opus-4-8',
    max_tokens: 2000,
    system: SYSTEM_PROMPT,
    tools: [SUBMIT_SCRIPT_TOOL],
    tool_choice: { type: 'tool', name: 'submit_script' },
    messages: [{ role: 'user', content: `Тема ролика: ${topic}` }],
  });

  const toolUse = response.content.find(
    (block): block is Anthropic.ToolUseBlock => block.type === 'tool_use'
  );
  if (!toolUse) {
    throw new Error('Claude не вернул вызов инструмента submit_script');
  }

  const input = toolUse.input as {
    language: string;
    lines: Array<{ section: ScriptSection; text: string; visual_prompt: string }>;
  };

  const lines: ScriptLine[] = input.lines.map((line, index) => ({
    index,
    section: line.section,
    text: line.text.trim(),
    visualPrompt: line.visual_prompt.trim(),
  }));

  return { topic, language: input.language, lines };
}
