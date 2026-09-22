/**
 * 客户端图片压缩(browser-image-compression)
 *
 * - 目标:长边 ≤ 2200px、JPEG 质量 0.85(保真与体积平衡,对手写识别友好);
 * - ZIP 与 HEIC/HEIF(浏览器无法解码)自动跳过;
 * - 任何失败自动回退原文件 —— 压缩是增强项,绝不阻断上传。
 */

import imageCompression from 'browser-image-compression'

const MAX_DIMENSION = 2200
const QUALITY = 0.85

export interface CompressResult {
  file: File
  originalSize: number
  compressedSize: number
  skipped: boolean
}

const SKIP_SUFFIXES = ['.zip', '.heic', '.heif']

/** 压缩单个文件(不可压缩/失败时原样返回) */
export async function compressImageFile(file: File): Promise<CompressResult> {
  const lower = file.name.toLowerCase()
  const compressible =
    (file.type.startsWith('image/') || /\.(jpe?g|png|webp|bmp)$/.test(lower)) &&
    !SKIP_SUFFIXES.some((suffix) => lower.endsWith(suffix))
  if (!compressible) {
    return { file, originalSize: file.size, compressedSize: file.size, skipped: true }
  }
  try {
    const compressed = await imageCompression(file, {
      maxWidthOrHeight: MAX_DIMENSION,
      initialQuality: QUALITY,
      useWebWorker: true,
    })
    if (!compressed || compressed.size >= file.size) {
      return { file, originalSize: file.size, compressedSize: file.size, skipped: true }
    }
    return {
      file: new File([compressed], file.name, { type: compressed.type || file.type || 'image/jpeg' }),
      originalSize: file.size,
      compressedSize: compressed.size,
      skipped: false,
    }
  } catch {
    return { file, originalSize: file.size, compressedSize: file.size, skipped: true }
  }
}

/** 批量压缩(串行,避免大量并发解码卡顿;onProgress 上报已完成数) */
export async function compressImageFiles(
  files: File[],
  onProgress?: (done: number, total: number) => void,
): Promise<File[]> {
  const results: File[] = []
  let done = 0
  for (const file of files) {
    const result = await compressImageFile(file)
    results.push(result.file)
    done += 1
    onProgress?.(done, files.length)
  }
  return results
}
