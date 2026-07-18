import path from 'node:path';
import axios from 'axios';
import { config } from '../config';
import { downloadFile, ensureDir } from '../utils/files';
import { describeAxiosError } from '../utils/http';

interface PexelsVideoFile {
  link: string;
  width: number;
  height: number;
  quality: string;
}

interface PexelsVideo {
  id: number;
  video_files: PexelsVideoFile[];
}

interface PexelsPhoto {
  id: number;
  src: { large2x?: string; original: string };
}

async function findBackgroundVideoUrl(query: string): Promise<string | null> {
  let data;
  try {
    ({ data } = await axios.get<{ videos: PexelsVideo[] }>(
      'https://api.pexels.com/videos/search',
      {
        headers: { Authorization: config.pexelsApiKey },
        params: { query, orientation: 'portrait', size: 'medium', per_page: 5 },
      }
    ));
  } catch (error) {
    throw new Error(`Pexels (видео): ${describeAxiosError(error)}`);
  }

  const portraitFiles = data.videos
    .flatMap((video) => video.video_files)
    .filter((file) => file.height > file.width && file.width > 0);

  if (portraitFiles.length === 0) return null;

  const best =
    portraitFiles.filter((f) => f.width >= 720).sort((a, b) => a.width - b.width)[0] ??
    portraitFiles[0];

  return best.link;
}

async function findBackgroundPhotoUrl(query: string): Promise<string | null> {
  let data;
  try {
    ({ data } = await axios.get<{ photos: PexelsPhoto[] }>('https://api.pexels.com/v1/search', {
      headers: { Authorization: config.pexelsApiKey },
      params: { query, orientation: 'portrait', per_page: 5 },
    }));
  } catch (error) {
    throw new Error(`Pexels (фото): ${describeAxiosError(error)}`);
  }

  const photo = data.photos[0];
  if (!photo) return null;
  return photo.src.large2x ?? photo.src.original;
}

/** Скачивает фоновое видео (приоритет) либо фото по текстовому запросу через Pexels. */
export async function downloadBackground(
  query: string,
  destDir: string,
  baseName: string
): Promise<{ filePath: string; kind: 'video' | 'image' }> {
  if (!config.pexelsApiKey) {
    throw new Error('PEXELS_API_KEY не задан в .env — нужен для скачивания фонового видео/фото');
  }

  await ensureDir(destDir);

  const videoUrl = await findBackgroundVideoUrl(query);
  if (videoUrl) {
    const filePath = path.join(destDir, `${baseName}.mp4`);
    await downloadFile(videoUrl, filePath);
    return { filePath, kind: 'video' };
  }

  const photoUrl = await findBackgroundPhotoUrl(query);
  if (photoUrl) {
    const filePath = path.join(destDir, `${baseName}.jpg`);
    await downloadFile(photoUrl, filePath);
    return { filePath, kind: 'image' };
  }

  throw new Error(`Pexels не нашёл видео/фото по запросу "${query}"`);
}
