import { spawnSync } from "node:child_process"
import { openSync, readSync, closeSync, existsSync } from "node:fs"
import { basename, extname } from "node:path"
import { homedir } from "node:os"
import { fileURLToPath } from "node:url"

export interface ImageAttachment { name: string; data_url: string }
const LIMIT = 5 * 1024 * 1024
const TYPES: Record<string, string> = { '.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp', '.gif': 'gif', '.bmp': 'bmp', '.tiff': 'tiff' }

function image(data: Buffer, name: string, type = 'png'): ImageAttachment {
  if (!data.length || data.length > LIMIT) throw new Error('Image exceeds 5 MiB or is empty / Изображение пустое или больше 5 МиБ')
  return { name, data_url: `data:image/${type};base64,${data.toString('base64')}` }
}

export function pathImage(text: string): ImageAttachment | null {
  let path = text.trim().replace(/^['"]|['"]$/g, '')
  if (path.startsWith('file://')) path = fileURLToPath(path)
  if (path.startsWith('~/')) path = homedir() + path.slice(1)
  const type = TYPES[extname(path).toLowerCase()]
  if (!type || !existsSync(path)) return null
  const fd = openSync(path, 'r')
  try {
    const data = Buffer.alloc(LIMIT + 1)
    const size = readSync(fd, data, 0, data.length, 0)
    return image(data.subarray(0, size), basename(path), type)
  } finally { closeSync(fd) }
}

export function clipboardImage(): ImageAttachment {
  let command: string, args: string[]
  if (process.platform === 'win32') {
    command = 'powershell.exe'
    args = ['-NoProfile', '-STA', '-Command', 'Add-Type -AssemblyName System.Windows.Forms; $i=[Windows.Forms.Clipboard]::GetImage(); if($null -eq $i){exit 1}; $m=New-Object IO.MemoryStream; $i.Save($m,[Drawing.Imaging.ImageFormat]::Png); [Console]::Write([Convert]::ToBase64String($m.ToArray()))']
  } else if (process.platform === 'darwin') {
    command = 'osascript'; args = ['-e', 'get the clipboard as «class PNGf»']
  } else if (process.env.WAYLAND_DISPLAY) {
    command = 'wl-paste'; args = ['--no-newline', '--type', 'image/png']
  } else {
    command = 'xclip'; args = ['-selection', 'clipboard', '-t', 'image/png', '-o']
  }
  const result = spawnSync(command, args, { timeout: 5000, maxBuffer: LIMIT * 3 })
  if (result.error || result.status !== 0) throw new Error('No clipboard image. Use /image PATH / Нет изображения в буфере; используйте /image ПУТЬ')
  let data = result.stdout
  if (process.platform === 'win32') data = Buffer.from(data.toString().trim(), 'base64')
  if (process.platform === 'darwin') data = Buffer.from(data.toString().trim().replace(/^«data PNGf|»$/g, ''), 'hex')
  return image(data, 'clipboard.png')
}

export function packImages(text: string, images: Map<number, ImageAttachment>): string {
  const selected = [...text.matchAll(/\[Image #(\d+)\]/g)].map(match => images.get(Number(match[1]))).filter((item): item is ImageAttachment => !!item)
  return selected.length ? JSON.stringify({ type: 'kvist-image-goal', text, images: [...new Set(selected)] }) : text
}
