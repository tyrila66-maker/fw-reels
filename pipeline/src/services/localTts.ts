import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';

/**
 * Озвучка без ключей и интернета — через встроенный в Windows TTS (System.Speech / SAPI).
 * Ищет установленный голос под язык реплики (по умолчанию русский), иначе берёт первый попавшийся.
 * Качество заметно хуже ElevenLabs, но полностью бесплатно и работает офлайн.
 */
export async function synthesizeSpeechLocal(
  text: string,
  destWavPath: string,
  cultureHint = 'ru',
  // Штатный Rate=0 говорит заметно медленнее, чем нужно для темпа Reels/TikTok;
  // rate=2 (~20% быстрее) даёт более "трендовый" темп, не жертвуя разборчивостью.
  rate = 2
): Promise<void> {
  await fs.mkdir(path.dirname(destWavPath), { recursive: true });

  const textFilePath = `${destWavPath}.input.txt`;
  await fs.writeFile(textFilePath, text, 'utf-8');

  const escapeForPs = (value: string) => value.replace(/'/g, "''");

  const psScript = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText('${escapeForPs(textFilePath)}', [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like '${escapeForPs(cultureHint)}*' } | Select-Object -First 1
if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }
$synth.Rate = ${rate}
$synth.SetOutputToWaveFile('${escapeForPs(destWavPath)}')
$synth.Speak($text)
$synth.Dispose()
`.trim();

  await new Promise<void>((resolve, reject) => {
    const proc = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', psScript]);
    let stderr = '';
    proc.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Локальный TTS (PowerShell) завершился с кодом ${code}:\n${stderr.slice(-1500)}`));
    });
  });

  await fs.unlink(textFilePath).catch(() => {});
}
