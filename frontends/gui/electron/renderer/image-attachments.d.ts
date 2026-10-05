export interface ImageAttachment { name: string; data_url: string }
export const MAX_IMAGES: number
export const MAX_IMAGE_BYTES: number
export function readImages(files: FileList | File[]): Promise<ImageAttachment[]>
export function imageGoal(text: string, images: ImageAttachment[]): string
