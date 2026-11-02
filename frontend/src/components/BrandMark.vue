<script setup>
defineProps({
  size: { type: Number, default: 36 },
  compact: { type: Boolean, default: false },
  light: { type: Boolean, default: false },
})
</script>

<template>
  <div class="brand" :class="{ 'brand--compact': compact, 'brand--light': light }">
    <span class="brand__mark" :style="{ width: `${size}px`, height: `${size}px` }">
      <svg viewBox="0 0 40 40" fill="none" aria-hidden="true">
        <path
          d="M13 24.6 20 14.4l7 10.2M16.2 20.6h7.6M12.6 29.2h14.8"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="square"
        />
        <circle cx="20" cy="9.4" r="1.5" fill="currentColor" />
      </svg>
    </span>
    <span v-if="!compact" class="brand__type">
      <strong>Ask<span>ToFit</span></strong>
      <small>Presales Atelier</small>
    </span>
  </div>
</template>

<style scoped>
.brand {
  display: inline-flex;
  align-items: center;
  color: var(--atf-ink);
  gap: 11px;
}

.brand__mark {
  display: grid;
  flex: none;
  color: var(--atf-on-brand);
  background: var(--atf-brand);
  border-radius: 2px;
  place-items: center;
}

.brand__mark svg {
  width: 82%;
  height: 82%;
}

.brand__type {
  display: flex;
  flex-direction: column;
  line-height: 1;
  gap: 4px;
}

.brand__type strong {
  font-family: var(--atf-font-display);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.brand__type strong span {
  color: var(--atf-brand);
}

.brand__type small {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  font-weight: 500;
  letter-spacing: 0.22em;
  text-transform: uppercase;
}

.brand--light {
  color: var(--atf-story-ink);
}

.brand--light .brand__mark {
  color: var(--atf-brand);
  background: var(--atf-story-ink);
}

.brand--light .brand__type strong span {
  color: var(--atf-story-accent);
}

.brand--light .brand__type small {
  color: var(--atf-story-soft);
}
</style>
