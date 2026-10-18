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
          d="M20 7.5c1.4 5.4 4.1 8.1 9.5 9.5-5.4 1.4-8.1 4.1-9.5 9.5-1.4-5.4-4.1-8.1-9.5-9.5 5.4-1.4 8.1-4.1 9.5-9.5Z"
          fill="currentColor"
        />
        <circle cx="29.4" cy="10.6" r="2.1" fill="currentColor" opacity="0.7" />
        <circle cx="10.8" cy="28.9" r="1.6" fill="currentColor" opacity="0.55" />
      </svg>
    </span>
    <span v-if="!compact" class="brand__type">
      <strong>Ask<span>ToFit</span></strong>
      <small>售前智能导购</small>
    </span>
  </div>
</template>

<style scoped>
.brand {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: var(--atf-ink);
}

.brand__mark {
  display: grid;
  flex: none;
  color: #fff;
  background: linear-gradient(135deg, #5b5bd6 4%, #8b7cf6 58%, #2dd4bf 130%);
  border-radius: 12px;
  box-shadow: 0 8px 18px rgba(91, 91, 214, 0.3);
  place-items: center;
}

.brand__mark svg {
  width: 78%;
  height: 78%;
}

.brand__type {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1;
}

.brand__type strong {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.03em;
}

.brand__type strong span {
  color: var(--atf-brand);
}

.brand__type small {
  margin-top: 4px;
  color: var(--atf-muted);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.18em;
}

.brand--light {
  color: #fff;
}

.brand--light .brand__type strong span {
  color: #d9d6ff;
}

.brand--light .brand__type small {
  color: rgba(255, 255, 255, 0.62);
}
</style>
