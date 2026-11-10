// Shared by Electron, the local web client and the IDE webview bundle.
export const MAX_IMAGES = 8
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024

export async function readImages(files) {
  const selected = Array.from(files).filter(file => file.type.startsWith('image/'))
  if (selected.length > MAX_IMAGES) throw new Error('Up to 8 images / Не больше 8 изображений')
  if (selected.reduce((size, file) => size + file.size, 0) > 20 * 1024 * 1024) throw new Error('Images exceed 20 MiB / Изображения больше 20 МиБ')
  return Promise.all(selected.map(file => {
    if (file.size > MAX_IMAGE_BYTES) throw new Error('Image exceeds 5 MiB / Изображение больше 5 МиБ')
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onerror = () => reject(new Error('Cannot read image / Не удалось прочитать изображение'))
      reader.onload = () => resolve({ name: file.name, data_url: String(reader.result) })
      reader.readAsDataURL(file)
    })
  }))
}

export function imageGoal(text, images) {
  return images.length ? JSON.stringify({ type: 'kvist-image-goal', text, images }) : text
}

export function mountImageAttachments(form, field, toolbar, onError) {
  let images = []
  let pending = Promise.resolve()
  let generation = 0
  const tray = document.createElement('div')
  tray.className = 'image-attachments'
  tray.setAttribute('aria-label', 'Attached images / Вложенные изображения')
  const picker = document.createElement('input')
  picker.type = 'file'
  picker.accept = 'image/png,image/jpeg,image/webp,image/gif,image/bmp,image/tiff'
  picker.multiple = true
  picker.hidden = true
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'image-attach-button'
  button.textContent = '+'
  button.title = 'Attach images / Прикрепить изображения'
  button.setAttribute('aria-label', button.title)
  button.onclick = () => { if (!field.disabled) picker.click() }
  toolbar.prepend(button)
  form.prepend(tray, picker)

  function render() {
    tray.replaceChildren()
    for (const [index, item] of images.entries()) {
      const tile = document.createElement('div')
      tile.className = 'image-attachment'
      tile.title = item.name
      const img = document.createElement('img')
      img.src = item.data_url
      img.alt = `Image #${index + 1}: ${item.name}`
      const remove = document.createElement('button')
      remove.type = 'button'
      remove.className = 'image-remove'
      remove.textContent = '×'
      remove.setAttribute('aria-label', `Remove image ${index + 1} / Удалить изображение`)
      remove.onclick = () => { images = images.filter(image => image !== item); render() }
      tile.append(img, remove)
      tray.append(tile)
    }
    tray.hidden = !images.length
  }

  function add(files) {
    const selected = Array.from(files)
    const stamp = generation
    pending = pending.then(async () => {
      const added = await readImages(selected)
      if (stamp !== generation) return
      if (images.length + added.length > MAX_IMAGES) throw new Error('Up to 8 images / Не больше 8 изображений')
      const next = [...images, ...added]
      if (next.reduce((size, item) => size + item.data_url.length, 0) > 28 * 1024 * 1024) throw new Error('Images exceed 20 MiB / Изображения больше 20 МиБ')
      images = next
      render()
    }).catch(error => onError(error.message))
  }
  picker.onchange = () => { add(picker.files || []); picker.value = '' }
  field.addEventListener('paste', event => {
    const files = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith('image/'))
    if (!files.length || field.disabled) return
    event.preventDefault()
    add(files)
  })
  form.addEventListener('dragover', event => { if (!field.disabled) event.preventDefault() })
  form.addEventListener('drop', event => {
    event.preventDefault()
    if (!field.disabled) add(event.dataTransfer?.files || [])
  })
  render()
  return {
    ready: () => pending,
    count: () => images.length,
    pack: text => imageGoal(text, images),
    labels: () => images.map((_, index) => `[Image #${index + 1}]`).join(' '),
    clear: () => { generation++; images = []; render() },
  }
}
