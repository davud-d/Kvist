// The Kvist mark drawn inline (the same shape as the plugin's toolbar icon), so the cursor under the
// chevron can blink once on arrival: the page is waiting for a request.
export function KvistMark({ size = 28, still = false }: { size?: number; still?: boolean }) {
  return (
    <svg class={`kvist-mark ${still ? "still" : ""}`} width={size} height={size} viewBox="0 0 512 512" role="img" aria-label="Kvist">
      <defs>
        <linearGradient id="kvist-mark-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#12303b" />
          <stop offset="1" stop-color="#0c1c24" />
        </linearGradient>
      </defs>
      <rect x="16" y="16" width="480" height="480" rx="112" fill="url(#kvist-mark-bg)" />
      <rect x="16" y="16" width="480" height="480" rx="112" fill="none" stroke="#477b93" stroke-opacity="0.35" stroke-width="4" />
      <path d="M150 150 L266 256 L150 362" fill="none" stroke="#43d3ff" stroke-width="46" stroke-linecap="round" stroke-linejoin="round" />
      <rect class="kvist-mark-cursor" x="286" y="332" width="118" height="42" rx="21" fill="#43d3ff" />
    </svg>
  )
}
