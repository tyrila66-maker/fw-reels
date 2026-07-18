import axios from 'axios';

/**
 * Безопасное описание ошибки axios: только HTTP-статус и тело ответа API.
 * Никогда не включает заголовки/конфиг запроса (там могут быть API-ключи).
 */
export function describeAxiosError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const data = error.response?.data;

    let detail: string;
    if (Buffer.isBuffer(data)) {
      detail = data.toString('utf-8');
    } else if (data) {
      detail = typeof data === 'string' ? data : JSON.stringify(data);
    } else {
      detail = error.message;
    }

    return `HTTP ${status ?? '?'}: ${detail}`;
  }

  return error instanceof Error ? error.message : String(error);
}
